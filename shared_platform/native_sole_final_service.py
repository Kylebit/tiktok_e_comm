"""Service-owned sole review channel; no actor/approval bootstrap from HTTP.

Construction belongs to startup. GET projection is only an existing SQLite
snapshot. Explicit review prepare/decision use the signed READ producer and
the automatically owned helper. Nothing migrates schemas or replays old tasks.
"""
from contextlib import contextmanager
from pathlib import Path
from dataclasses import asdict
import json
import sqlite3
import threading
import time
from collections import deque
from urllib.parse import urlsplit

from shared_platform.final_review_server_admission import ApprovalBlocked
from shared_platform.release_store import ReleaseStore, PLAN_PENDING_APPROVAL, PLAN_APPROVED
from shared_platform.native_sole_final_decision import NativeSoleFinalDecisionService, check_schema
from shared_platform.native_actor_helper import NativeActorHelperSupervisor
from shared_platform.native_actor_authentication import NativeHelperActorProfileReader
from shared_platform.native_windows_actor import NativeActorServiceConfig

PREFIX='/api/product-workspace/native-final/'


# Diagnostics are projections only: no raw exception text or arbitrary class/code.
_SAFE_PHASES=frozenset({'DEQUEUED','TASK_RUNNING_PROJECTION','PUBLICATION_SCOPE',
    'PUBLICATION_ACTOR_RECHECK','PUBLICATION_EXECUTION','PROMOTION_ACTOR_RECHECK',
    'PROMOTION_EXECUTION','READBACK_OBSERVATION','TASK_RECORDED_PROJECTION','WORKER_SETUP','STAGE_PROJECTION'})
_SAFE_ERROR_TYPES=frozenset({'ApprovalBlocked','ValueError','OSError','KeyError','TypeError',
    'RuntimeError','Exception','ImportError','AttributeError','LookupError','TimeoutError',
    'PermissionError','FileNotFoundError','OperationalError','IntegrityError','ProgrammingError',
    'DatabaseError','InterfaceError','DataError','NotSupportedError','ReturnedError'})
_SAFE_ERROR_CODES=frozenset({'NATIVE_EXPLICIT_EXECUTION_FAILED','NATIVE_TASK_PROGRESS_PROJECTION_FAILED',
    'NATIVE_SOLE_FINAL_STAGE_PROJECTION_FAILED','DOMAIN_REVIEW_DISPLAY_OR_PAYLOAD_CHANGED',
    'NATIVE_HELPER_CLIENT_RESULT_UNKNOWN','NATIVE_SOLE_FINAL_CURRENT_COMMON_READ_UNVERIFIED',
    'NATIVE_SOLE_FINAL_CURRENT_ROUNDS_CHANGED','NATIVE_SOLE_FINAL_OWNER_IDENTITY_UNVERIFIED',
    'NATIVE_SOLE_FINAL_EXECUTION_OWNER_CHANGED','NATIVE_SOLE_FINAL_EXECUTION_IDENTITY_CHANGED',
    'NATIVE_SOLE_FINAL_EXECUTION_SCOPE_CHANGED','NATIVE_SOLE_FINAL_COMMON_DEPENDENCY_CHANGED',
    'NATIVE_SOLE_FINAL_ATOMIC_APPROVAL_CHANGED','NATIVE_SOLE_FINAL_SERVICE_CLOSED',
    'NATIVE_SOLE_FINAL_SERVICE_CONTEXT_MISSING','NATIVE_SOLE_FINAL_SERVICE_CONTEXT_CHANGED',
    'NATIVE_SOLE_FINAL_SCHEMA_NOT_INSTALLED','NATIVE_SOLE_FINAL_OFFER_CHANGED',
    'NATIVE_SOLE_FINAL_COMMON_BUDGET_UNVERIFIED','NATIVE_SOLE_FINAL_STANDING_POLICY_UNKNOWN',
    'NATIVE_SOLE_FINAL_PLAN_NOT_CURRENT','NATIVE_SOLE_FINAL_DECISION_REQUIRED',
    'NATIVE_SOLE_FINAL_GENUINE_COMMON_CONTEXT_REQUIRED','NATIVE_NEW_DECISION_EXECUTION_NOT_INSTALLED',
    'NATIVE_TASK_ACTIVE_OWNER_REQUIRES_RECONCILIATION','NATIVE_TASK_PROGRESS_COMPARE_AND_SWAP_CHANGED',
    'NATIVE_SOLE_FINAL_OWNER_CHANGED_DURING_RECHECK','LOCAL_OPERATOR_SESSION_EXPIRED'})
_SAFE_SQLITE_NAMES=frozenset(name for name,value in vars(sqlite3).items()
    if name.startswith('SQLITE_') and type(value) is int)

def _safe_error_code(value):
    return value if type(value) is str and value in _SAFE_ERROR_CODES else None

def _safe_worker_outcome(outcome,phase,error=None):
    status,value=outcome if type(outcome) is tuple and len(outcome)==2 else (None,{})
    value=value if type(value) is dict else {}
    safe={'http_status':status if type(status) is int and 100<=status<=599 else None,
          'phase':phase if phase in _SAFE_PHASES else 'WORKER_SETUP'}
    code=_safe_error_code(value.get('error'))
    if error is not None:
        kind=type(error).__name__
        safe['error_type']=kind if kind in _SAFE_ERROR_TYPES else 'Exception'
        code=_safe_error_code(str(error))
        if isinstance(error,sqlite3.Error):
            sqlite_name=getattr(error,'sqlite_errorname',None)
            if type(sqlite_name) is str and sqlite_name in _SAFE_SQLITE_NAMES:
                safe['sqlite_errorname']=sqlite_name
    elif type(status) is int and status>=400:
        safe['error_type']='ReturnedError'
    if code is not None:safe['error_code']=code
    return safe

def _worker_failure(decision_id,error):
    return 409,{'ok':False,'error':_safe_error_code(str(error)) or 'NATIVE_EXPLICIT_EXECUTION_FAILED',
        'approval_saved':True,'external_write_outcome':'UNKNOWN','new_human_review_required':False,
        'native_decision_id':decision_id}


def check_current_rounds(store,plan_id,*,operations=None):
    from shared_platform import publication_r3_image_bridge as bridge
    from shared_platform.workbench_publication_native import load_service_r2_documents
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        row=db.execute('SELECT payload_json FROM release_plans WHERE plan_id=?',(plan_id,)).fetchone()
        if row is None:raise ApprovalBlocked('NATIVE_SOLE_FINAL_PLAN_MISSING')
        payload=json.loads(row['payload_json'])
        if operations is not None:
            operations.engine.assert_publication_scope_consumable(payload['product_id'], payload=payload)
        binding=payload['r3_marketplace_binding']
        documents=load_service_r2_documents(payload['product_id'],operations)
        if bridge.validate_r2_identity(documents)!=binding['r2_identity']:
            raise ApprovalBlocked('NATIVE_SOLE_FINAL_CURRENT_ROUNDS_CHANGED')


class NativeSoleFinalService:
    def __init__(self,store,config,*,new_decision_execution_enabled=False,operations=None):
        if type(store) is not ReleaseStore or type(config) is not NativeActorServiceConfig:
            raise ApprovalBlocked('NATIVE_SOLE_FINAL_SERVICE_CONFIG_REQUIRED')
        self.store=store
        if type(new_decision_execution_enabled) is not bool:
            raise ApprovalBlocked('NATIVE_SOLE_FINAL_RUNTIME_SCOPE_INVALID')
        # Fixed trusted startup owns this scope. It cannot be enabled by a
        # request, environment value, legacy token, or a persisted decision.
        self._new_decision_execution_enabled=new_decision_execution_enabled
        self.supervisor=NativeActorHelperSupervisor(config)
        self._lock=threading.RLock()
        self._reader=None
        self._closed=False
        self._execution_lock=threading.RLock()
        self._execution_thread=None
        self._execution_decision=None
        self._execution_result=None
        self._execution_queue=deque()
        self._execution_results={}
        self._execution_phase=None
        self._execution_diagnostics={}
        self._promotion_diagnostics={}
        self._active_promotion_targets=None
        self._promotion_queue=deque()
        self._promotion_results={}
        self._readback_schedule={}
        self._work_available=threading.Event()
        self._stopping=False
        self._worker_running=False
        self._execution_busy_decision=None
        self._approval_reservations=0
        self._operations=operations

    def start(self):
        # Startup, never a GET, owns creation. Existing UNKNOWN retirement rules
        # retain handles and prevent replacement; there is no catch-and-replay.
        with self._lock:
            self._reader=self._fresh_reader()
        return self

    def _fresh_reader(self):
        carrier=self.supervisor.request_carrier()
        reader=NativeHelperActorProfileReader(self.supervisor,carrier)
        reader.read_verified()
        return reader

    @contextmanager
    def operation(self):
        with self._lock:
            if self._closed:raise ApprovalBlocked('NATIVE_SOLE_FINAL_SERVICE_CLOSED')
            if self._reader is None:
                self._reader=self._fresh_reader()
            else:
                endpoint=self.supervisor.endpoint()
                if endpoint!=self._reader.endpoint:
                    # The supervisor has proven the original owned generation's
                    # retirement before returning a different endpoint.
                    self._reader=self._fresh_reader()
                else:
                    try:self._reader.read_verified()
                    except ApprovalBlocked as error:
                        if str(error)!='LOCAL_OPERATOR_SESSION_EXPIRED':raise
                        self._reader=self._fresh_reader()
            yield NativeSoleFinalDecisionService(self.store,self._reader)

    def prepare(self,plan_id):
        self._require_original_plan_scope(plan_id)
        with self.operation() as service:
            check_current_rounds(self.store,plan_id,operations=self._operations)
            return service.prepare(plan_id)

    def _require_original_plan_scope(self, plan_id):
        adopted=getattr(self._operations,'agent_prepared_common',None)
        if adopted is not None:
            from shared_platform.agent_prepared_common_service import AgentPreparedCommonService
            if type(adopted) is not AgentPreparedCommonService:
                raise ApprovalBlocked('ADOPTION_CONCRETE_SERVICE_REQUIRED')
            task,record=adopted._current()
            from shared_platform.prepared_target_consumer import effective_marketplace_targets
            effective=effective_marketplace_targets(record['offer_id'],record['targets'])
            with self.store._connect_readonly() as db:
                row=db.execute('SELECT product_id,target_labels_json FROM release_plans WHERE plan_id=?',(plan_id,)).fetchone()
            if (row is None or row['product_id']!=record['offer_id']
                    or sorted(json.loads(row['target_labels_json']))!=sorted(effective)):
                raise ApprovalBlocked('ADOPTION_NATIVE_PLAN_OUTSIDE_FROZEN_SCOPE')
        binding=getattr(self._operations,'original_continuation',None)
        if binding is None:return
        from shared_platform.original_prepared_continuation import OriginalPreparedTaskContinuation
        from shared_platform.prepared_target_consumer import configured_target_consumer
        if type(binding) is not OriginalPreparedTaskContinuation:
            raise ApprovalBlocked('ORIGINAL_CONTINUATION_CONCRETE_BINDING_REQUIRED')
        binding.verify_sources()
        with self.store._connect_readonly() as db:
            db.execute('BEGIN')
            row=db.execute('SELECT product_id,payload_json FROM release_plans WHERE plan_id=?',(plan_id,)).fetchone()
            if row is None or row['product_id']!=binding.pins.offer_id:
                raise ApprovalBlocked('ORIGINAL_NATIVE_PLAN_OUTSIDE_BOUND_OFFER')
            consumer=configured_target_consumer(binding.pins.offer_id)
            if consumer is None:raise ApprovalBlocked('ORIGINAL_NATIVE_TARGET_CONSUMER_REQUIRED')
            consumer.validate_payload(json.loads(row['payload_json']))

    def _require_original_decision_scope(self, decision_id):
        if (getattr(self._operations,'original_continuation',None) is None
                and getattr(self._operations,'agent_prepared_common',None) is None):return
        with self.store._connect_readonly() as db:
            row=db.execute('SELECT plan_id FROM native_sole_final_decisions WHERE decision_id=?',(decision_id,)).fetchone()
            if row is None:raise ApprovalBlocked('ORIGINAL_NATIVE_DECISION_REQUIRED')
        self._require_original_plan_scope(row['plan_id'])

    def prepare_common_for_task(self,engine,task,token,profile,binding,plan):
        """The original workflow's native COMMON-first path, no human approve.

        The managed ledger owns account/coverage checks and UNKNOWN before
        transport. This service only binds the genuine current task/OS owner.
        It never sets READY or substitutes an approval for missing facts.
        """
        from modules.products import server
        from shared_platform import workbench_publication_native as native
        from shared_platform import operations_publication_common as common
        original=getattr(self._operations,'original_continuation',None)
        if original is not None and (task.get('task_id')!=original.pins.task_id
                or task.get('worker')!=original.pins.original_worker):
            raise ApprovalBlocked('ORIGINAL_CONTINUATION_ORIGINAL_WRITER_REQUIRED')
        if self._closed or not self._new_decision_execution_enabled:
            raise ApprovalBlocked('NATIVE_COMMON_TASK_RUNTIME_NOT_INSTALLED')
        if self._operations is None or engine is not self._operations.engine or profile is not self._operations.profile:
            raise ApprovalBlocked('NATIVE_COMMON_TASK_RUNTIME_CHANGED')
        if profile.environment!='stable':raise ApprovalBlocked('NATIVE_COMMON_TASK_STABLE_RUNTIME_REQUIRED')
        if engine.release != task.get('version') or engine.get(task['task_id']) != task:
            raise ApprovalBlocked('NATIVE_COMMON_TASK_CURRENT_IDENTITY_CHANGED')
        with engine.transaction() as db:
            row=engine._lease(db,task['task_id'],token)
            if json.loads(row['scope_json'])!=task['scope']:
                raise ApprovalBlocked('NATIVE_COMMON_TASK_CURRENT_SCOPE_CHANGED')
        if (binding.get('preparation_source') or {}).get('status')!='VERIFIED_NATIVE_R1':
            raise ApprovalBlocked('COMMON_PREPARATION_SOURCE_UNKNOWN')
        frozen=native.read_frozen(task,profile)
        if frozen is None or frozen['snapshot_digest']!=binding['round1_snapshot_digest']:
            raise ApprovalBlocked('NATIVE_COMMON_TASK_SOURCE_CHANGED')
        with self.operation() as service:service._actor()
        if server._release_store().path.resolve()!=self.store.path.resolve():
            raise ApprovalBlocked('NATIVE_COMMON_TASK_STORE_CHANGED')
        current=common._read(server,task)
        candidate=(current.get('common') or {}).get('plan') or {}
        if (common._unknown(current)
                or common._verified_binding(task,current.get('common') or {},profile)!=binding
                or candidate.get('plan_id')!=plan.get('plan_id')
                or candidate.get('payload_digest')!=plan.get('payload_digest')
                or candidate.get('payload')!=plan.get('payload')):
            raise ApprovalBlocked('NATIVE_COMMON_TASK_CURRENT_CANDIDATE_CHANGED')
        # The explicit, owner-bound preparation owns this inert reservation.
        # GET/readiness never creates a plan or an approval.
        with engine.transaction() as db:
            engine._lease(db,task['task_id'],token)
        reserved=self.store.create_plan(candidate['payload'])
        if (reserved['plan_id']!=candidate['plan_id']
                or reserved['payload_digest']!=candidate['payload_digest']
                or reserved['payload']!=candidate['payload']
                or reserved['status']!=PLAN_PENDING_APPROVAL):
            raise ApprovalBlocked('NATIVE_COMMON_TASK_PERSISTED_CANDIDATE_CHANGED')
        with engine.transaction() as db:
            engine._lease(db,task['task_id'],token)
        code,result=server._prepare_native_common_ledger({'offer_id':binding['offer_id'],
            'plan_id':plan['plan_id'],'confirm_miaoshou_write':True},self.store)
        if code!=200:
            diagnostic={'status':'BLOCKED','binding':binding,
                'blockers':[result.get('error') or 'COMMON_TECHNICAL_RESULT_UNPROVEN'],
                'native_run':result.get('native_run'),'native_attempt_not_redispatched':True}
            return common._block_missing_common_authority(engine,task,token,diagnostic)
        after=common._read(server,task)
        if common._verified_binding(task,after['common'],profile)!=binding:
            engine.observe_reconciliation(task['task_id'],token,'原生 COMMON 返回身份变化，不重复写入')
            return False
        # Getter presents the actual retained technical completion, while the
        # materializer rereads its exact same-store record to build the matrix.
        return common._materialize_or_block(engine,server,task,token,after,profile)

    def final_review_readiness(self,task,view):
        """Reread the real matrix and completed baseline, never a caller READY."""
        from modules.products import server
        from shared_platform import operations_publication_common as common
        if self._operations is None or self._closed:
            raise ApprovalBlocked('NATIVE_COMMON_TASK_RUNTIME_NOT_INSTALLED')
        binding=common._verified_binding(task,view.get('common') or {},self._operations.profile)
        if (binding.get('preparation_source') or {}).get('status')!='VERIFIED_NATIVE_R1':
            raise ApprovalBlocked('COMMON_PREPARATION_SOURCE_UNKNOWN')
        plan=(view.get('marketplace') or {}).get('plan') or {}
        code,current=server._publication_stages_for_request({'offer_id':binding['offer_id'],
            'plan_id':plan.get('plan_id')})
        actual=current.get('marketplace') or {};review=actual.get('native_final_review') or {}
        from shared_platform.prepared_target_consumer import configured_target_consumer
        consumer=configured_target_consumer(task['scope']['offer_id'])
        expected_targets=[label for label in task['scope']['shops'] if label!='miaoshou:COMMON']
        if consumer is not None:
            expected_targets=consumer.read_effective_scope(task['scope']['offer_id'])['shops']
        if (code!=200 or current.get('ok') is not True
                or common._verified_binding(task,current.get('common') or {},self._operations.profile)!=binding
                or actual.get('status')!='APPROVAL_REQUIRED'
                or actual.get('final_review_available') is not True
                or review.get('schema_version')!='native-sole-final-review/v1'
                or review.get('approval_saved') is not False
                or review.get('plan_id')!=plan.get('plan_id')
                or sorted(review.get('targets') or [])!=expected_targets
                or review.get('execution_authority') is not False
                or not review.get('review_digest')):
            raise ApprovalBlocked('NATIVE_COMMON_COMPLETE_FINAL_REVIEW_UNPROVEN')
        return {'schema_version':'post-common-native-readiness/v1','status':'READY',
            'blockers':[],'final_review_available':True,'execution_authority':False,
            'receipt_digest':review['review_digest'],'external_writes_performed':[]}

    def common_request_readiness(self,task,view):
        """Existing service facts may release an observation; never reserve here."""
        from shared_platform import operations_publication_common as common
        from shared_platform import native_common_technical_execution as technical
        from shared_platform.native_sole_final_decision import _hash
        if self._operations is None or self._closed:
            raise ApprovalBlocked('NATIVE_COMMON_TASK_RUNTIME_NOT_INSTALLED')
        binding=common._verified_binding(task,view.get('common') or {},self._operations.profile)
        if (binding.get('preparation_source') or {}).get('status')!='VERIFIED_NATIVE_R1':
            raise ApprovalBlocked('COMMON_PREPARATION_SOURCE_UNKNOWN')
        with self.store._connect_readonly() as db:
            db.execute('BEGIN')
            if not technical._schema_installed(db):
                raise ApprovalBlocked('COMMON_TECHNICAL_SCHEMA_NOT_INSTALLED')
            facts=technical._service_facts(self.store,db,binding['plan_id'])
            if facts.payload_digest!=binding['payload_digest'] or facts.offer_id!=binding['offer_id']:
                raise ApprovalBlocked('NATIVE_COMMON_TASK_SOURCE_CHANGED')
            receipt=_hash(technical._bytes(facts.binding()))
        return {'status':'READY','receipt_digest':receipt,'execution_authority':False,
            'blockers':[],'external_writes_performed':[]}

    def decide(self,*,nonce,review_digest):
        with self.operation() as service:
            with self.store._connect_readonly() as db:
                db.execute('BEGIN')
                from shared_platform.native_sole_final_decision import _hash
                row=db.execute('SELECT plan_id FROM native_sole_final_nonces WHERE nonce_digest=?',
                    (_hash(nonce.encode()),)).fetchone() if type(nonce) is str else None
                if row is None:raise ApprovalBlocked('NATIVE_SOLE_FINAL_NONCE_INVALID')
                plan_id=row['plan_id']
            self._require_original_plan_scope(plan_id)
            check_current_rounds(self.store,plan_id,operations=self._operations)
            # A page TTL or a genuine helper-session refresh is technical
            # recovery of these exact displayed bytes, never another decision.
            value=service.refresh_nonce(nonce=nonce,review_digest=review_digest)
            if value.get('decision_id'):return value
            return service.decide(nonce=value['nonce'],review_digest=review_digest)

    def resume(self,decision_id):
        self._set_execution_phase('PUBLICATION_SCOPE')
        self._require_original_decision_scope(decision_id)
        from shared_platform.native_sole_final_execution import execute_decision
        self._set_execution_phase('PUBLICATION_ACTOR_RECHECK')
        with self.operation() as service:pass
        self._set_execution_phase('PUBLICATION_EXECUTION')
        return execute_decision(service,decision_id,runtime=self)

    def submit_media_verification(self,decision_id,proof_ref):
        """Existing trust fence, local technical evidence only; never dispatch."""
        from shared_platform.native_sole_final_execution import _NativeExecution, _ACTIVE
        from shared_platform.native_ozon_three_card_media_recovery import DECISION, PLAN, intake_verification
        if decision_id!=DECISION:raise ApprovalBlocked('NATIVE_SOLE_FINAL_ORIGINAL_MEDIA_DECISION_REQUIRED')
        self._require_original_decision_scope(decision_id)
        with self._execution_lock:
            if (self._execution_busy_decision==decision_id or decision_id in self._execution_queue
                    or decision_id in self._readback_schedule):
                return 409,{'ok':False,'error':'NATIVE_EXECUTION_OWNER_BUSY','external_writes_performed':[]}
            with self.operation() as service:
                active=_NativeExecution(service,decision_id,self);active.plan_id=PLAN
                token=_ACTIVE.set(active)
                try:intake_verification(active,proof_ref)
                finally:_ACTIVE.reset(token)
        return 200,{'ok':True,'native_decision_id':decision_id,'technical_media_verification_recorded':True,
            'new_human_review_required':False,'execution_authority':False,'external_writes_performed':[]}

    def submit_promotions(self,decision_id,target_labels):
        """Same explicit native route/FIFO; publication cache is never replayed."""
        from shared_platform.native_companion_promotions import selected_targets
        labels=selected_targets(target_labels)
        self._require_original_decision_scope(decision_id)
        with self.operation() as service:
            actor=service._actor()
            with self.store._connect_readonly() as db:
                db.execute('BEGIN')
                service._decision(db,decision_id,owner_sid=actor['owner_sid'])
        if self._closed or not self._new_decision_execution_enabled:
            raise ApprovalBlocked('NATIVE_NEW_DECISION_EXECUTION_NOT_INSTALLED')
        key=(decision_id,labels)
        with self._execution_lock:
            if self._stopping:raise ApprovalBlocked('NATIVE_EXECUTION_OWNER_STOPPING')
            if (self._execution_busy_decision is not None or self._execution_queue
                    or self._readback_schedule):
                raise ApprovalBlocked('NATIVE_PUBLICATION_OR_READBACK_STILL_RUNNING')
            if key in self._promotion_results:return self._promotion_results[key]
            if key not in self._promotion_queue:
                if self._pending_count()>=32:
                    raise ApprovalBlocked('NATIVE_EXPLICIT_DECISION_QUEUE_FULL')
                self._promotion_queue.append(key)
            self._start_owned_worker()
        return 202,{'ok':True,'native_decision_id':decision_id,
            'promotion_targets':list(labels),'promotion_execution_queued':True,
            'approval_saved':True,'new_human_review_required':False,
            'external_writes_performed':[]}

    def submit_readback(self,decision_id):
        """Explicit original-decision READ only; never enters the WRITE FIFO."""
        self._require_original_decision_scope(decision_id)
        with self.operation() as service:
            actor=service._actor()
            with self.store._connect_readonly() as db:
                db.execute('BEGIN')
                service._decision(db,decision_id,owner_sid=actor['owner_sid'])
                row=db.execute('SELECT plan_id FROM native_sole_final_decisions WHERE decision_id=?',
                               (decision_id,)).fetchone()
                runrow=db.execute('SELECT run_id FROM release_runs WHERE plan_id=?',(row['plan_id'],)).fetchone()
                run=self.store._run_in_transaction(db,runrow['run_id']) if runrow else None
        from shared_platform.native_sole_final_readback import eligible_shopee_failed_readback
        if (not any(target['status']=='SUBMITTED_UNVERIFIED' for target in (run or {}).get('targets') or [])
                and not eligible_shopee_failed_readback(self.store,decision_id)):
            raise ApprovalBlocked('NATIVE_ACCEPTED_SUBMISSION_REQUIRED_FOR_READ_ONLY_RESUME')
        with self._execution_lock:
            if self._closed or self._stopping:
                raise ApprovalBlocked('NATIVE_EXECUTION_OWNER_STOPPING')
            if decision_id in self._execution_queue or self._execution_busy_decision==decision_id:
                raise ApprovalBlocked('NATIVE_ORIGINAL_DECISION_STILL_RUNNING')
            if decision_id not in self._readback_schedule:
                if self._pending_count()>=32:
                    raise ApprovalBlocked('NATIVE_EXPLICIT_READBACK_QUEUE_FULL')
                self._readback_schedule[decision_id]=(time.monotonic(),1)
            self._start_owned_worker()
        return 202,{'ok':True,'native_decision_id':decision_id,'readonly_recheck':True,
                    'approval_saved':True,'new_human_review_required':False,
                    'external_writes_performed':[]}

    def approve_and_submit(self,*,nonce,review_digest):
        # Serialize queue capacity with this explicit approval. A full queue
        # does not first consume the nonce and permanently strand a decision.
        with self._execution_lock:
            if self._stopping:raise ApprovalBlocked('NATIVE_EXECUTION_OWNER_STOPPING')
            if self._pending_count()>=32:
                from shared_platform.native_sole_final_decision import _hash
                with self.store._connect_readonly() as db:
                    existing=db.execute('SELECT decision_id FROM native_sole_final_nonces WHERE nonce_digest=?',
                        (_hash(nonce.encode()),)).fetchone() if type(nonce) is str else None
                if existing is None or not existing['decision_id']:
                    raise ApprovalBlocked('NATIVE_EXPLICIT_DECISION_QUEUE_FULL_BEFORE_APPROVAL')
            self._approval_reservations+=1
        try:
            # No execution lock is held during actor/current official READ.
            result=self.decide(nonce=nonce,review_digest=review_digest)
            code,outcome=self.submit(result['decision_id'],_reserved_slot=True)
            return code,{'ok':code in {200,202},'decision':result,'approval_saved':True,**outcome}
        finally:
            with self._execution_lock:self._approval_reservations-=1

    def submit(self,decision_id,*,_reserved_slot=False):
        """Only this explicit verified POST enters one bounded owned FIFO."""
        self._require_original_decision_scope(decision_id)
        with self.operation() as service:
            actor=service._actor()
            with self.store._connect_readonly() as db:
                db.execute('BEGIN')
                service._decision(db,decision_id,owner_sid=actor['owner_sid'])
        if self._closed or not self._new_decision_execution_enabled:
            return 409,{'ok':False,'error':'NATIVE_NEW_DECISION_EXECUTION_NOT_INSTALLED',
                'approval_saved':True,'new_human_review_required':False,'external_writes_performed':[]}
        with self._execution_lock:
            if self._stopping:
                raise ApprovalBlocked('NATIVE_EXECUTION_OWNER_STOPPING')
            if decision_id in self._execution_queue or decision_id in self._readback_schedule or self._execution_busy_decision==decision_id:
                return 202,{'ok':True,'approval_saved':True,'execution_queued':True,
                    'native_decision_id':decision_id,'execution_owner_decision':self._execution_decision,
                    'new_human_review_required':False,'external_writes_performed':[]}
            if decision_id in self._execution_results:
                previous=self._execution_results[decision_id]
                prior=previous[1]
                rows=(prior.get('run') or {}).get('targets') or []
                from modules.products import server
                if (not self._known_zero_actor_failure(decision_id, prior, actor['owner_sid'])
                        and not _has_exact_existing_continuation(self.store,decision_id,operations=self._operations)
                        and not any(row.get('status')=='PENDING' or server._generic_tiktok_safe_retry_target(row)
                                    for row in rows)):
                    if (prior.get('readonly_observation') or {}).get('pending') is True:
                        # A new explicit resume may renew only the bounded READ
                        # schedule, never replay the already accepted writes.
                        self._readback_schedule[decision_id]=(time.monotonic(),1)
                        self._start_owned_worker()
                        return 202,{'ok':True,'approval_saved':True,'execution_queued':True,
                            'readonly_recheck':True,'native_decision_id':decision_id,
                            'new_human_review_required':False,'external_writes_performed':[]}
                    return previous
            if self._pending_count()>=32 and not _reserved_slot:
                return 409,{'ok':False,'approval_saved':True,'error':'NATIVE_EXPLICIT_DECISION_QUEUE_FULL',
                    'new_human_review_required':False,'native_decision_id':decision_id,
                    'execution_queued':False,'external_writes_performed':[]}
            self._execution_queue.append(decision_id)
        from shared_platform.native_sole_final_tasks import project_explicit_decision_progress
        try:project_explicit_decision_progress(self,decision_id,'QUEUED')
        except (ValueError,OSError,KeyError,TypeError,RuntimeError) as error:
            # Approval and explicit ownership are already durable. Retain
            # work instead of silently dropping it on a UI projection error.
            with self._execution_lock:
                self._execution_results[decision_id]=(202,{'ok':True,'task_progress_error':str(error)})
        with self._execution_lock:
            self._start_owned_worker()
        return 202,{'ok':True,'approval_saved':True,'execution_queued':True,
            'native_decision_id':decision_id,'new_human_review_required':False,'external_writes_performed':[]}

    def _known_zero_actor_failure(self, decision_id, prior, owner_sid):
        # submit() has just verified the current protected actor. A failed
        # identity RPC is reusable only when the original durable executor
        # never created a run or reserved a provider mutation for this plan.
        if prior.get('error') != 'NATIVE_HELPER_CLIENT_RESULT_UNKNOWN':
            return False
        with self.store._connect_readonly() as db:
            db.execute('BEGIN')
            row = db.execute(
                'SELECT p.plan_id,p.status,p.superseded_by_plan_id FROM native_sole_final_decisions d '
                'JOIN release_plans p ON p.plan_id=d.plan_id WHERE d.decision_id=? AND d.owner_sid=?',
                (decision_id, owner_sid)).fetchone()
            if row is None or row['status'] != PLAN_APPROVED or row['superseded_by_plan_id'] is not None:
                return False
            if db.execute('SELECT 1 FROM release_runs WHERE plan_id=? LIMIT 1', (row['plan_id'],)).fetchone():
                return False
            for table in ('native_final_budget_reservations',
                          'native_final_budget_dispatches', 'native_final_budget_outcomes'):
                if db.execute(
                        'SELECT 1 FROM native_final_budget_scopes s JOIN '+table+' r '
                        'ON r.scope_key=s.scope_key WHERE s.plan_id=? LIMIT 1', (row['plan_id'],)).fetchone():
                    return False
        return True

    def _start_owned_worker(self):
        # Caller holds the same execution lock as enqueue/empty-exit/CAS.
        self._work_available.set()
        if not self._worker_running:
            self._worker_running=True
            self._execution_thread=threading.Thread(target=self._run_explicit_queue,
                name='native-final-explicit-decision',daemon=False)
            self._execution_thread.start()

    def _pending_count(self):
        return self._approval_reservations+len(set(self._execution_queue)|set(self._readback_schedule)
                   |({self._execution_busy_decision} if self._execution_busy_decision else set()))+len(self._promotion_queue)

    def _set_execution_phase(self,phase):
        with self._execution_lock:self._execution_phase=phase

    def _project_worker_diagnostics(self,market,decision_id):
        with self._execution_lock:
            diagnostic=self._execution_diagnostics.get(decision_id)
            if diagnostic is not None:market['native_execution_last_outcome']=dict(diagnostic)
            promotion=self._promotion_diagnostics.get(decision_id)
            if promotion is not None:market['native_promotion_last_outcome']=dict(promotion)
            market['native_execution_current_phase']=(self._execution_phase
                if self._execution_busy_decision==decision_id and self._execution_phase in _SAFE_PHASES else None)

    def _run_explicit_queue(self):
        try:self._process_explicit_queue()
        except Exception as error:
            # Last-resort cache before finally clears ownership. No retry/requeue.
            with self._execution_lock:
                decision_id=self._execution_busy_decision
                promotion_targets=self._active_promotion_targets
                phase=self._execution_phase
                if decision_id is None:
                    # Imports/setup can fail before dequeue. Retain queued work;
                    # record its failure without silently retrying or claiming it.
                    phase='WORKER_SETUP'
                    if self._execution_queue:decision_id=self._execution_queue[0]
                    elif self._promotion_queue:
                        decision_id,promotion_targets=self._promotion_queue[0]
                    elif self._readback_schedule:decision_id=next(iter(self._readback_schedule))
                if decision_id is not None:
                    outcome=_worker_failure(decision_id,error)
                    diagnostic=_safe_worker_outcome(outcome,phase,error)
                    if promotion_targets is not None:
                        self._promotion_results[(decision_id,promotion_targets)]=outcome
                        self._promotion_diagnostics[decision_id]=diagnostic
                    else:
                        self._execution_results[decision_id]=outcome
                        self._execution_result=outcome
                        self._execution_diagnostics[decision_id]=diagnostic
        finally:
            with self._execution_lock:
                if self._execution_thread is threading.current_thread():
                    self._worker_running=False
                    self._execution_busy_decision=None
                    self._execution_phase=None
                    self._active_promotion_targets=None

    def _process_explicit_queue(self):
        from shared_platform.native_sole_final_tasks import project_explicit_decision_progress
        from shared_platform.native_sole_final_readback import observe_submissions
        while True:
            with self._execution_lock:
                self._work_available.clear()
                if self._stopping:
                    self._worker_running=False
                    return
                promotion_targets=None
                if self._execution_queue:
                    decision_id=self._execution_queue.popleft()
                    read_round=None
                elif self._promotion_queue:
                    decision_id,promotion_targets=self._promotion_queue.popleft()
                    read_round=None
                else:
                    due=next(((key,value) for key,value in self._readback_schedule.items()
                        if value[0]<=time.monotonic()),None)
                    if due is None:
                        if not self._readback_schedule:
                            # The lock prevents a POST observing this worker as
                            # alive after its final empty check and losing work.
                            self._worker_running=False
                            return
                        delay=max(0,min(value[0] for value in self._readback_schedule.values())-time.monotonic())
                        decision_id=None
                    else:
                        decision_id,(when,read_round)=due
                        del self._readback_schedule[decision_id]
                if decision_id is not None:
                    self._execution_decision=decision_id
                    self._execution_busy_decision=decision_id
                    self._active_promotion_targets=promotion_targets
                    self._execution_phase='DEQUEUED'
                    self._execution_result=self._execution_results.get(decision_id)
                    if read_round is None and promotion_targets is None:self._execution_results.pop(decision_id,None)
            if decision_id is None:
                self._work_available.wait(timeout=delay)
                continue
            error=None
            try:
                if promotion_targets is not None:
                    from shared_platform.native_sole_final_execution import execute_decision
                    self._set_execution_phase('PROMOTION_ACTOR_RECHECK')
                    with self.operation() as service:pass
                    self._set_execution_phase('PROMOTION_EXECUTION')
                    outcome=execute_decision(service,decision_id,runtime=self,
                        promotion_targets=promotion_targets)
                    observations=None
                elif read_round is None:
                    self._set_execution_phase('TASK_RUNNING_PROJECTION')
                    project_explicit_decision_progress(self,decision_id,'RUNNING')
                    outcome=self.resume(decision_id)
                    observations=None
                    needs_read=any(row.get('status')=='SUBMITTED_UNVERIFIED'
                        for row in (outcome[1].get('run') or {}).get('targets') or [])
                    if needs_read:
                        with self._execution_lock:
                            self._readback_schedule[decision_id]=(time.monotonic(),1)
                else:
                    outcome=self._execution_results.get(decision_id,(200,{
                        'ok':True,'approval_saved':True,'native_decision_id':decision_id,
                        'new_human_review_required':False,'external_writes_performed':[]}))
                    self._set_execution_phase('READBACK_OBSERVATION')
                    observations=observe_submissions(self,decision_id)
                if observations is not None:
                    code,value=outcome
                    updated=observations.get('run') or value.get('run')
                    complete=bool(updated) and all(row.get('status')=='SUCCEEDED' and row.get('readback')
                        for row in updated.get('targets') or [])
                    outcome=(code,{**value,'run':updated,'completed':complete,
                        'readonly_observation':observations,'automatic_read_round':read_round,
                        'readback_pending':not complete,'new_human_review_required':False})
                    if observations.get('pending') and read_round<3:
                        with self._execution_lock:
                            self._readback_schedule[decision_id]=(time.monotonic()+2,read_round+1)
            except Exception as caught:
                error=caught
                outcome=_worker_failure(decision_id,error)
            diagnostic=_safe_worker_outcome(outcome,self._execution_phase,error)
            if promotion_targets is not None:
                with self._execution_lock:
                    self._promotion_results[(decision_id,promotion_targets)]=outcome
                    self._promotion_diagnostics[decision_id]=diagnostic
                    self._execution_busy_decision=None
                    self._execution_phase=None
                continue
            with self._execution_lock:
                self._execution_results[decision_id]=outcome
                self._execution_result=outcome
                self._execution_diagnostics[decision_id]=diagnostic
                if len(self._execution_results)>128:
                    old=next((key for key in self._execution_results if key!=decision_id
                        and key not in self._execution_queue and key not in self._readback_schedule),None)
                    if old is not None:
                        del self._execution_results[old]
                        self._execution_diagnostics.pop(old,None)
            try:
                self._set_execution_phase('TASK_RECORDED_PROJECTION')
                project_explicit_decision_progress(self,decision_id,'RECORDED')
            except Exception as error:
                code,value=outcome
                with self._execution_lock:
                    self._execution_diagnostics[decision_id]=_safe_worker_outcome(outcome,'TASK_RECORDED_PROJECTION',error)
                    self._execution_results[decision_id]=(code,{**value,'task_progress_error':_safe_error_code(str(error)) or 'NATIVE_TASK_PROGRESS_PROJECTION_FAILED',
                        'task_progress_requires_reconciliation':True})
                    self._execution_result=self._execution_results[decision_id]
            with self._execution_lock:
                self._execution_busy_decision=None
                self._execution_phase=None

    def project(self,stages):
        """Full frozen matrix from the same store; no IPC, READ, DB creation."""
        from shared_platform.r3_common_source_facts import NativeCommonSourceReader
        from shared_platform.r3_frozen_review_producer import read_stored_domain_graph
        from shared_platform.native_common_budget_facts import census_same_snapshot
        from shared_platform import native_common_edit_boundary as boundary
        from modules.products import server
        if not self.store.path.is_file():return stages
        value=dict(stages);market=dict(value.get('marketplace') or {})
        plan=market.get('plan') or {}
        if not plan.get('plan_id'):return stages
        decision_id=None
        try:
            if value.get('current_r2_matches_approved') is not True:
                raise ApprovalBlocked('NATIVE_SOLE_FINAL_CURRENT_ROUNDS_CHANGED')
            with self.store._connect_readonly() as db:
                db.execute('BEGIN')
                check_schema(db)
                decision=db.execute('SELECT decision_id FROM native_sole_final_decisions WHERE plan_id=?',(plan['plan_id'],)).fetchone()
                decision_id=decision['decision_id'] if decision else None
                self._project_worker_diagnostics(market,decision_id)
                reader=NativeCommonSourceReader(self.store)
                graph=read_stored_domain_graph(db,plan['plan_id'],native_reader=reader)
                if graph.offer_id!=str(value.get('offer_id')):
                    raise ApprovalBlocked('NATIVE_SOLE_FINAL_OFFER_CHANGED')
                common=db.execute('SELECT * FROM release_plans WHERE plan_id=?',(graph.common_plan_id,)).fetchone()
                facts=reader.read_source_facts(db,graph.common_plan_id)
                census=census_same_snapshot(self.store,db,json.loads(common['payload_json']),facts)
                from shared_platform.native_common_baseline_successor import unresolved_after_authorized_successor
                if (census['status']!='LOCAL_PERSISTED_CENSUS' or unresolved_after_authorized_successor(self.store,db,json.loads(common['payload_json']),census)
                        or census['local_observed_confirmed_writes']!=1
                        or any(r.get('same_preparation_root') is True for r in census['unclassified_history'])):
                    raise ApprovalBlocked('NATIVE_SOLE_FINAL_COMMON_BUDGET_UNVERIFIED')
                active=boundary.service_boundary()
                if active is None:raise ApprovalBlocked('NATIVE_SOLE_FINAL_SERVICE_CONTEXT_MISSING')
                active.same_snapshot_coverage(self.store,db,graph.common_plan_id)
                signing=server._service_common_signing_context_reader(self.store)
                policy=server._service_common_standing_policy_reader()
                if signing is None or policy is None or signing._config is not active._config or policy._config is not active._config:
                    raise ApprovalBlocked('NATIVE_SOLE_FINAL_SERVICE_CONTEXT_CHANGED')
                from shared_platform.publication_common_standing_policy import inspect_service_policy
                known=inspect_service_policy(policy)
                if known.status!='KNOWN_LOCAL_USER_INTENT' or known.maximum_confirmed_writes!=1:
                    raise ApprovalBlocked('NATIVE_SOLE_FINAL_STANDING_POLICY_UNKNOWN')
                persisted=db.execute('SELECT status FROM release_plans WHERE plan_id=?',(plan['plan_id'],)).fetchone()
                if persisted['status'] not in {PLAN_PENDING_APPROVAL,PLAN_APPROVED}:
                    raise ApprovalBlocked('NATIVE_SOLE_FINAL_PLAN_NOT_CURRENT')
                if (persisted['status']==PLAN_APPROVED)!=(decision is not None):
                    raise ApprovalBlocked('NATIVE_SOLE_FINAL_DECISION_REQUIRED')
                material=json.loads(graph.display_bytes)
                from shared_platform.native_common_retained_completion import read_retained_completion
                from shared_platform import native_common_technical_execution as technical
                from shared_platform.native_sole_final_decision import _bytes, _hash
                from shared_platform.final_review_server_admission import FrozenReview
                completion=read_retained_completion(reader,db,graph.common_plan_id,graph.common_run_id)
                run=db.execute('SELECT * FROM release_runs WHERE run_id=?',(graph.common_run_id,)).fetchone()
                credential=technical._binding(run)
                context=credential.get('native_account_context')
                if context is None:raise ApprovalBlocked('NATIVE_SOLE_FINAL_GENUINE_COMMON_CONTEXT_REQUIRED')
                budget={'scope':'IMMUTABLE_PREPARED_BASELINE','root_id':completion.preparation_root_id,
                    'r1_account_digest':completion.account_identity_digest,
                    'signing_context_digest':context['common_signing_context_digest'],
                    'mutation_sha256':credential['mutation_sha256'],'maximum_confirmed_writes':1,
                    'confirmed_writes':census['local_observed_confirmed_writes']}
                review=FrozenReview(graph.offer_id,graph.marketplace_plan_id,graph.candidate_digest,
                    graph.critical_content_digest,graph.targets,graph.common_plan_id,graph.common_run_id,
                    graph.common_readback_digest,'sha256:'+_hash(_bytes(budget)),graph.round1_digest,
                    graph.round2_digest,'native-final:'+_hash(_bytes([graph.offer_id,plan['plan_id']])))
                run_row=db.execute('SELECT run_id FROM release_runs WHERE plan_id=?',(plan['plan_id'],)).fetchone()
                market_run=self.store._run_in_transaction(db,run_row['run_id']) if run_row else None
                result_rows=[]
                for target in (market_run or {}).get('targets') or []:
                    partial_stock=target.get('partial_stock_result')
                    evidence=partial_stock or target.get('readback') or target.get('submission') or target.get('failure') or {}
                    state=target['status']
                    # A durable SUCCEEDED row only exists after the original
                    # target readback validator. Submissions remain unverified.
                    result_rows.append({'target_label':target['target_label'],'status':state,
                        'source':'ReleaseStore:'+market_run['run_id'],
                        'request_attempted':target['attempts']>0,
                        'official_success':state=='SUCCEEDED' and bool(target.get('readback')),
                        'readback_completed':state=='SUCCEEDED' and bool(target.get('readback')),
                         'outcome_unknown':partial_stock['outcome_unknown'] if partial_stock else state in {'RUNNING','RECONCILIATION_REQUIRED'} or (
                             state=='FAILED' and target['attempts']>0
                             and not server._generic_tiktok_safe_retry_target(target)),
                        'readback':evidence})
                uncertain=any(row['outcome_unknown'] for row in result_rows)
                executed=bool(result_rows) and all(row['request_attempted'] or row['official_success'] for row in result_rows)
                completed=bool(result_rows) and all(row['official_success'] for row in result_rows)
                market.update(target_results=result_rows,execution_summary={
                    'index_valid':True,'blockers':['NATIVE_MARKETPLACE_OUTCOME_UNKNOWN'] if uncertain else []},
                    final_review={'approval_recorded':bool(decision),
                        'approval_id':(plan.get('approval') or {}).get('approval_id'),
                        'candidate_digest':graph.candidate_digest,'execution_recorded':executed})
                market['native_runtime_scope']={
                    'scope':'EXPLICIT_NATIVE_DECISION_ONLY',
                    'enabled':self._new_decision_execution_enabled and not self._closed,
                    'historical_task_scan':False,'legacy_dispatcher_enabled':False,
                    'requires_current_recheck':True}
                with self._execution_lock:
                    decision_id=decision['decision_id'] if decision else None
                    market['native_execution_in_progress']=(bool(decision) and
                        self._worker_running
                        and (decision_id in self._execution_queue or decision_id in self._readback_schedule
                            or self._execution_decision==decision_id))
                    market['native_execution_queued']=(decision_id in self._execution_queue)
                    self._project_worker_diagnostics(market,decision_id)
                    last=(self._execution_results.get(decision_id) or (None,{}))[1]
                    observations=(last.get('readonly_observation') or {}).get('observations') or []
                    market['native_readback_resume_available']=(bool(decision)
                        and not market['native_execution_in_progress']
                        and any(row['status']=='SUBMITTED_UNVERIFIED' for row in result_rows))
                    market['native_readback_capabilities']=[{'target_label':item.get('target_label'),
                        'capability':item.get('capability'),'error':item.get('error')}
                        for item in observations if item.get('capability') or item.get('error')]
                market['native_technical_details']={'previous_preparation_blockers':market.get('blockers') or [],
                    'provider_edit_permission':'UNKNOWN','execution_authority':False}
                market['blockers']=['NATIVE_MARKETPLACE_OUTCOME_UNKNOWN'] if uncertain else []
                market.update(preview=material,targets=list(graph.targets),
                    status=('RECONCILIATION_REQUIRED' if uncertain else 'PUBLISHED' if completed else
                            'PROCESSING' if executed else 'NATIVE_DECISION_SAVED') if decision else 'APPROVAL_REQUIRED',
                    final_review_available=not bool(decision),
                    final_review_admission={'final_review_available':not bool(decision),'execution_authority':False},
                    native_final_review={'schema_version':'native-sole-final-review/v1',
                        'plan_id':graph.marketplace_plan_id,'offer_id':graph.offer_id,
                        'candidate_digest':graph.candidate_digest,'targets':list(graph.targets),
                        'review':asdict(review),'review_digest':review.digest(),
                        'decision_id':decision['decision_id'] if decision else None,
                        'approval_saved':bool(decision),'execution_authority':False})
                value['common']={**(value.get('common') or {}),'status':'VERIFIED'}
                value['marketplace']=market
                return value
        except Exception as error:
            market['final_review_available']=False
            market['final_review_admission']={'final_review_available':False,'execution_authority':False}
            market['native_final_review']={'status':'BLOCKED',
                'error':_safe_error_code(str(error)) or 'NATIVE_SOLE_FINAL_STAGE_PROJECTION_FAILED',
                'diagnostic':_safe_worker_outcome((409,{}),'STAGE_PROJECTION',error),'execution_authority':False}
            value['marketplace']=market
            return value

    def close(self):
        with self._execution_lock:
            self._stopping=True
            self._work_available.set()
        thread=self._execution_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5)
            if thread.is_alive():
                # Preserve the helper and execution owner's strong references.
                raise ApprovalBlocked('NATIVE_EXECUTION_RETIREMENT_UNKNOWN')
        with self._lock:
            self._closed=True
            return self.supervisor.close()


def handle_native_final(handler,*,method):
    """Existing loopback/Origin and bounded duplicate-rejecting body contract."""
    path=urlsplit(handler.path)
    if not path.path.startswith(PREFIX):return False
    from shared_platform.local_operator_http import PrivateLocalReviewHttp
    from shared_platform.release_store import ReleaseStoreError
    installed=getattr(handler.server,'native_final_review',None)
    try:
        if type(installed) is not NativeSoleFinalService:
            raise ApprovalBlocked('NATIVE_SOLE_FINAL_SERVICE_NOT_INSTALLED')
        if path.query or path.fragment or method!='POST':
            raise ApprovalBlocked('NATIVE_SOLE_FINAL_EXPLICIT_ACTION_REQUIRED')
        # Reuse the existing pure request fence, not its private authority.
        PrivateLocalReviewHttp._bound(installed,handler,write=True)
        body=PrivateLocalReviewHttp._body(installed,handler)
        if path.path==PREFIX+'prepare' and set(body)=={'plan_id'}:
            result=installed.prepare(body['plan_id'])
        elif path.path==PREFIX+'decision' and set(body)=={'nonce','review_digest'}:
            from modules.products import server
            code,outcome=server._approve_r3_marketplace_stage(body,native_service=installed)
            handler._json(code,outcome)
            return True
        elif path.path==PREFIX+'resume' and set(body)=={'decision_id','promotion_targets'}:
            code,result=installed.submit_promotions(body['decision_id'],body['promotion_targets'])
            handler._json(code,result);return True
        elif path.path==PREFIX+'resume' and set(body)=={'decision_id','media_verification_ref'}:
            code,result=installed.submit_media_verification(body['decision_id'],body['media_verification_ref'])
            handler._json(code,result);return True
        elif path.path==PREFIX+'resume' and set(body)=={'decision_id'}:
            from modules.products import server
            code,result=server._resume_r3_marketplace_stage(body,native_service=installed)
            handler._json(code,result);return True
        elif path.path==PREFIX+'readback' and set(body)=={'decision_id'}:
            code,result=installed.submit_readback(body['decision_id'])
            handler._json(code,result);return True
        else:raise ApprovalBlocked('NATIVE_SOLE_FINAL_FIELDS_INVALID')
        handler._json(200,{'ok':True,**result,'external_writes_performed':[]})
    except (ValueError,OSError,KeyError,TypeError,RuntimeError,ReleaseStoreError) as error:
        handler._json(409,{'ok':False,'error':str(error),'execution_authority':False,'external_writes_performed':[]})
    return True


def _has_exact_existing_continuation(store,decision_id,*,operations=None):
    """After submit's current actor/decision READ, inspect exact local work only.

    This adds no execution authority: original protected activate, server lock,
    begin_target and per-wire ledger/current checks remain the only write path.
    """
    from shared_platform.native_final_mutation_budget import NativeMutationBudgetHold
    from shared_platform.native_shopee_known_zero_recovery import has_existing_item_work
    from shared_platform.native_ozon_existing_media_recovery import has_verified_stock_work
    from shared_platform.native_ozon_three_card_media_recovery import has_pending_work
    from shared_platform.native_tiktok_locale_draft_correction import has_pending_work as has_tiktok_locale_work
    from shared_platform.native_shopee_regional_continuation import has_pending_work as has_shopee_regional_work
    try:
        return (has_existing_item_work(store,decision_id) or has_verified_stock_work(store,decision_id)
            or has_pending_work(store,decision_id,operations)
            or has_tiktok_locale_work(store,decision_id,operations)
            or has_shopee_regional_work(store,decision_id,operations))
    except (NativeMutationBudgetHold,ValueError,OSError,KeyError,TypeError):
        return False
