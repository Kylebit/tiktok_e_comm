"""Service-owned sole review channel; no actor/approval bootstrap from HTTP.

Construction belongs to startup. GET projection is only an existing SQLite
snapshot. Explicit review prepare/decision use the signed READ producer and
the automatically owned helper. Nothing migrates schemas or replays old tasks.
"""
from contextlib import contextmanager
from pathlib import Path
from dataclasses import asdict
import json
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


def check_current_rounds(store,plan_id,*,operations=None):
    from shared_platform import publication_r3_image_bridge as bridge
    from shared_platform.workbench_publication_native import load_service_r2_documents
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        row=db.execute('SELECT payload_json FROM release_plans WHERE plan_id=?',(plan_id,)).fetchone()
        if row is None:raise ApprovalBlocked('NATIVE_SOLE_FINAL_PLAN_MISSING')
        payload=json.loads(row['payload_json'])
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
        with self.operation() as service:
            check_current_rounds(self.store,plan_id,operations=self._operations)
            return service.prepare(plan_id)

    def prepare_common_for_task(self,engine,task,token,profile,binding,plan):
        """The original workflow's native COMMON-first path, no human approve.

        The managed ledger owns account/coverage checks and UNKNOWN before
        transport. This service only binds the genuine current task/OS owner.
        It never sets READY or substitutes an approval for missing facts.
        """
        from modules.products import server
        from shared_platform import workbench_publication_native as native
        from shared_platform import operations_publication_common as common
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
        if (code!=200 or current.get('ok') is not True
                or common._verified_binding(task,current.get('common') or {},self._operations.profile)!=binding
                or actual.get('status')!='APPROVAL_REQUIRED'
                or actual.get('final_review_available') is not True
                or review.get('schema_version')!='native-sole-final-review/v1'
                or review.get('approval_saved') is not False
                or review.get('plan_id')!=plan.get('plan_id')
                or sorted(review.get('targets') or [])!=[
                    label for label in task['scope']['shops'] if label!='miaoshou:COMMON']
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
            check_current_rounds(self.store,plan_id,operations=self._operations)
            # A page TTL or a genuine helper-session refresh is technical
            # recovery of these exact displayed bytes, never another decision.
            value=service.refresh_nonce(nonce=nonce,review_digest=review_digest)
            if value.get('decision_id'):return value
            return service.decide(nonce=value['nonce'],review_digest=review_digest)

    def resume(self,decision_id):
        from shared_platform.native_sole_final_execution import execute_decision
        with self.operation() as service:pass
        return execute_decision(service,decision_id,runtime=self)

    def submit_readback(self,decision_id):
        """Explicit original-decision READ only; never enters the WRITE FIFO."""
        with self.operation() as service:
            actor=service._actor()
            with self.store._connect_readonly() as db:
                db.execute('BEGIN')
                service._decision(db,decision_id,owner_sid=actor['owner_sid'])
                row=db.execute('SELECT plan_id FROM native_sole_final_decisions WHERE decision_id=?',
                               (decision_id,)).fetchone()
                runrow=db.execute('SELECT run_id FROM release_runs WHERE plan_id=?',(row['plan_id'],)).fetchone()
                run=self.store._run_in_transaction(db,runrow['run_id']) if runrow else None
        if not any(target['status']=='SUBMITTED_UNVERIFIED' for target in (run or {}).get('targets') or []):
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
                if (not any(row.get('status')=='PENDING' or server._generic_tiktok_safe_retry_target(row)
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
                   |({self._execution_busy_decision} if self._execution_busy_decision else set()))

    def _run_explicit_queue(self):
        try:self._process_explicit_queue()
        finally:
            with self._execution_lock:
                if self._execution_thread is threading.current_thread():
                    self._worker_running=False
                    self._execution_busy_decision=None

    def _process_explicit_queue(self):
        from shared_platform.native_sole_final_tasks import project_explicit_decision_progress
        from shared_platform.native_sole_final_readback import observe_submissions
        while True:
            with self._execution_lock:
                self._work_available.clear()
                if self._stopping:
                    self._worker_running=False
                    return
                if self._execution_queue:
                    decision_id=self._execution_queue.popleft()
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
                    self._execution_result=self._execution_results.get(decision_id)
                    if read_round is None:self._execution_results.pop(decision_id,None)
            if decision_id is None:
                self._work_available.wait(timeout=delay)
                continue
            try:
                if read_round is None:
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
            except (ValueError,OSError,KeyError,TypeError,RuntimeError) as error:
                outcome=(409,{'ok':False,'error':str(error),'approval_saved':True,
                    'external_write_outcome':'UNKNOWN','new_human_review_required':False,
                    'native_decision_id':decision_id})
            with self._execution_lock:
                self._execution_results[decision_id]=outcome
                self._execution_result=outcome
                if len(self._execution_results)>128:
                    old=next((key for key in self._execution_results if key!=decision_id
                        and key not in self._execution_queue and key not in self._readback_schedule),None)
                    if old is not None:del self._execution_results[old]
            try:project_explicit_decision_progress(self,decision_id,'RECORDED')
            except (ValueError,OSError,KeyError,TypeError,RuntimeError) as error:
                code,value=outcome
                with self._execution_lock:
                    self._execution_results[decision_id]=(code,{**value,'task_progress_error':str(error),
                        'task_progress_requires_reconciliation':True})
                    self._execution_result=self._execution_results[decision_id]
            with self._execution_lock:
                self._execution_busy_decision=None

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
        try:
            if value.get('current_r2_matches_approved') is not True:
                raise ApprovalBlocked('NATIVE_SOLE_FINAL_CURRENT_ROUNDS_CHANGED')
            with self.store._connect_readonly() as db:
                db.execute('BEGIN')
                check_schema(db)
                reader=NativeCommonSourceReader(self.store)
                graph=read_stored_domain_graph(db,plan['plan_id'],native_reader=reader)
                if graph.offer_id!=str(value.get('offer_id')):
                    raise ApprovalBlocked('NATIVE_SOLE_FINAL_OFFER_CHANGED')
                common=db.execute('SELECT * FROM release_plans WHERE plan_id=?',(graph.common_plan_id,)).fetchone()
                facts=reader.read_source_facts(db,graph.common_plan_id)
                census=census_same_snapshot(self.store,db,json.loads(common['payload_json']),facts)
                if (census['status']!='LOCAL_PERSISTED_CENSUS' or census['unresolved_attempts']
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
                decision=db.execute('SELECT decision_id FROM native_sole_final_decisions WHERE plan_id=?',(plan['plan_id'],)).fetchone()
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
                    evidence=target.get('readback') or target.get('submission') or target.get('failure') or {}
                    state=target['status']
                    # A durable SUCCEEDED row only exists after the original
                    # target readback validator. Submissions remain unverified.
                    result_rows.append({'target_label':target['target_label'],'status':state,
                        'source':'ReleaseStore:'+market_run['run_id'],
                        'request_attempted':target['attempts']>0,
                        'official_success':state=='SUCCEEDED' and bool(target.get('readback')),
                        'readback_completed':state=='SUCCEEDED' and bool(target.get('readback')),
                         'outcome_unknown':state in {'RUNNING','RECONCILIATION_REQUIRED'} or (
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
        except (ValueError,OSError,KeyError,TypeError,RuntimeError) as error:
            market['final_review_available']=False
            market['final_review_admission']={'final_review_available':False,'execution_authority':False}
            market['native_final_review']={'status':'BLOCKED','error':str(error),'execution_authority':False}
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
