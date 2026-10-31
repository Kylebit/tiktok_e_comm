"""Native decision recheck around the existing durable per-target executor.

Only an exact service-owned decision consumer opens this transient scope. It
does not authorize caller tokens or bypass private/superseded/approval guards.
All target claims/results remain the original ReleaseStore transactions.
"""
from contextvars import ContextVar
from contextlib import contextmanager
import json
from pathlib import Path

from shared_platform.final_review_server_admission import ApprovalBlocked
from shared_platform.native_sole_final_decision import NativeSoleFinalDecisionService

_ACTIVE=ContextVar('native_sole_final_execution',default=None)


class _NativeExecution:
    def __init__(self,service,decision_id,runtime,*,platform_scope=None):
        if type(service) is not NativeSoleFinalDecisionService:
            raise ApprovalBlocked('NATIVE_SOLE_FINAL_SERVICE_CONSUMER_REQUIRED')
        self.service=service
        self.runtime=runtime
        self.store=service.store
        self.decision_id=decision_id
        self.owner_sid=service._actor()['owner_sid']
        self.plan_id=None
        if platform_scope is not None and (type(platform_scope) is not tuple or len(platform_scope)!=1
                or platform_scope[0] not in {'TIKTOK','SHOPEE','OZON'}):
            raise ApprovalBlocked('NATIVE_PLATFORM_PARTITION_INVALID')
        self.platform_scope=platform_scope

    def gate(self,data,*,store):
        try:return self._gate(data,store=store)
        except (ValueError,OSError,KeyError,TypeError,RuntimeError) as error:
            # The original executor appends prior real outcomes when a later
            # target's fresh gate fails. Do not erase already attempted writes.
            return None,(409,{'ok':False,'error':str(error),'execution_authority':False,
                              'external_writes_performed':[]})

    def _gate(self,data,*,store):
        """Recheck complete graph/current READ immediately before each claim."""
        from modules.products import server,release_adapters
        if store is not self.store or _ACTIVE.get() is not self:
            raise ApprovalBlocked('NATIVE_SOLE_FINAL_EXECUTION_SCOPE_CHANGED')
        from shared_platform.native_sole_final_service import check_current_rounds
        if self.plan_id is not None:check_current_rounds(self.store,self.plan_id,operations=self.runtime._operations)
        with self.runtime.operation() as current:
            if current._actor()['owner_sid']!=self.owner_sid:
                raise ApprovalBlocked('NATIVE_SOLE_FINAL_EXECUTION_OWNER_CHANGED')
            checked=current.recheck(self.decision_id)
            self.service=current
        with self.store._connect_readonly() as db:
            db.execute('BEGIN')
            decision=db.execute('SELECT plan_id,owner_sid FROM native_sole_final_decisions WHERE decision_id=?',
                                (self.decision_id,)).fetchone()
            if decision is None or decision['owner_sid']!=self.owner_sid:
                raise ApprovalBlocked('NATIVE_SOLE_FINAL_EXECUTION_OWNER_CHANGED')
            self.plan_id=decision['plan_id']
            from shared_platform.r3_common_source_facts import NativeCommonSourceReader
            from shared_platform.native_common_retained_completion import read_retained_completion
            stored=db.execute('SELECT payload_json FROM release_plans WHERE plan_id=?',
                              (self.plan_id,)).fetchone()
            binding=json.loads(stored['payload_json'])['r3_marketplace_binding']
            common_completion=read_retained_completion(NativeCommonSourceReader(self.store),db,
                binding['common_plan_id'],binding['common_run_id'])
            if (common_completion.offer_id!=str(data['offer_id'])
                    or common_completion.readback_digest!=binding['common_readback']['evidence_digest']):
                raise ApprovalBlocked('NATIVE_SOLE_FINAL_COMMON_DEPENDENCY_CHANGED')
        plan=self.store.get_plan(self.plan_id)
        if (data['plan_id']!=plan['plan_id'] or data['confirmation_token']!=plan['confirmation_token']
                or data['offer_id']!=plan['product_id']):
            raise ApprovalBlocked('NATIVE_SOLE_FINAL_EXECUTION_IDENTITY_CHANGED')
        payload=plan['payload']
        if 'miaoshou:COMMON' in plan['targets']:
            raise ApprovalBlocked('NATIVE_SOLE_FINAL_MARKETPLACE_TARGETS_REQUIRED')
        registry=release_adapters.production_adapter_registry()
        if getattr(self.runtime._operations,'agent_prepared_common',None) is not None:
            from shared_platform.agent_prepared_v4_targets import registry_for_adopted
            registry=registry_for_adopted(self,plan,registry)
        elif (getattr(self.runtime._operations,'original_continuation',None) is None
                and getattr(self.runtime._operations,'new_task_worker',None) is not None):
            from shared_platform.native_new_task_v4_targets import registry_for_new_task
            registry=registry_for_new_task(self,plan,registry)
        else:
            from shared_platform.native_prepared_v4_targets import registry_for_original
            registry=registry_for_original(self,plan,registry)
        rows=[];partition_labels=[]
        for label in plan['targets']:
            channel,site=label.split(':',1)
            adapter=release_adapters.ADAPTER_NAMES.get(channel)
            if adapter is None:raise ApprovalBlocked('NATIVE_SOLE_FINAL_TARGET_ADAPTER_UNKNOWN')
            if self.platform_scope is not None and channel.upper() not in self.platform_scope:continue
            partition_labels.append(label)
            rows.append({'channel':channel,'site':site,'adapter':adapter})
        if not rows:raise ApprovalBlocked('NATIVE_PLATFORM_PARTITION_EMPTY')
        run=self.store.get_run('release-run:'+plan['payload_digest'][:24])
        value={'dashboard':{'frozen_review_projection':True,
                    'product':{'offer_id':plan['product_id'],'title':payload['product_facts']['title']},
                    'publication_scope':{'selected_labels':list(plan['targets'])}},
                'payload':payload,'plan':plan,'run':run,'predecessor_run':None,
                'registry':registry,'target_rows':rows,'native_recheck':checked,
                'common_completion':common_completion}
        if self.platform_scope is not None:
            value['execution_partition']={'platform':self.platform_scope[0],'target_labels':partition_labels}
        return value,None

    def common_dependency_verified(self,gate):
        from shared_platform.native_common_retained_completion import RetainedCommonCompletion
        completion=gate.get('common_completion')
        binding=gate['payload']['r3_marketplace_binding']
        return (type(completion) is RetainedCommonCompletion
            and completion.plan_id==binding['common_plan_id']
            and completion.run_id==binding['common_run_id']
            and completion.offer_id==str(gate['plan']['product_id'])
            and completion.readback_digest==binding['common_readback']['evidence_digest']
            and completion.state in {'CONFIRMED_WRITE','READONLY_REUSE'})


def allows_native_run(db,plan_id):
    """Same transaction approval/owner/snapshot check for every original write.

The legacy function still rejects private authority before reaching this hook.
An installed native row or a request dict cannot create this execution scope.
"""
    active=_ACTIVE.get()
    if type(active) is not _NativeExecution or active.plan_id!=plan_id:return False
    database=next((row[2] for row in db.execute('PRAGMA database_list') if row[1]=='main'),None)
    if database is None or Path(database).resolve()!=active.store.path.resolve():
        raise ApprovalBlocked('NATIVE_SOLE_FINAL_EXECUTION_STORE_CHANGED')
    active.service._decision(db,active.decision_id,owner_sid=active.owner_sid)
    return True


def execute_decision(service,decision_id,*,runtime=None,promotion_targets=None):
    from modules.products import server
    if type(service) is not NativeSoleFinalDecisionService:
        raise ApprovalBlocked('NATIVE_SOLE_FINAL_SERVICE_CONSUMER_REQUIRED')
    from shared_platform.native_sole_final_service import NativeSoleFinalService
    if type(runtime) is not NativeSoleFinalService or runtime.store is not service.store:
        raise ApprovalBlocked('NATIVE_SOLE_FINAL_RUNTIME_SCOPE_REQUIRED')
    if runtime._closed or not runtime._new_decision_execution_enabled:
        return 409,{'ok':False,'error':'NATIVE_NEW_DECISION_EXECUTION_NOT_INSTALLED',
                    'approval_saved':True,'new_human_review_required':False,
                    'external_writes_performed':[]}
    # This exact startup-owned scope only consumes an explicit native decision.
    # It does not open the legacy global gate, scan tasks, or start a dispatcher.
    active=_NativeExecution(service,decision_id,runtime)
    token=_ACTIVE.set(active)
    try:
        with service.store._connect_readonly() as db:
            db.execute('BEGIN')
            receipt=service._decision(db,decision_id,owner_sid=active.owner_sid)
            row=db.execute('SELECT plan_id FROM native_sole_final_decisions WHERE decision_id=?',(decision_id,)).fetchone()
            plan_id=row['plan_id']
        plan=service.store.get_plan(plan_id)
        active.plan_id=plan['plan_id']
        data={'offer_id':plan['product_id'],'plan_id':plan['plan_id'],
            'confirmation_token':plan['confirmation_token'],'confirm_publish':True}
        # The existing executor holds its original non-reentrant execution lock.
        # It creates/reuses the run inside that lock after this gate succeeds.
        if promotion_targets is not None:
            from shared_platform.native_companion_promotions import execute_active
            return execute_active(active,data,promotion_targets)
        operations=runtime._operations
        if (getattr(operations,'original_continuation',None) is None
                and getattr(operations,'agent_prepared_common',None) is None
                and getattr(operations,'new_task_worker',None) is not None):
            from shared_platform.native_new_task_v4_targets import scope
            scope(active,plan)  # Original worker/task/R1/R2 before scheduling.
            gate,failure=active.gate(data,store=service.store)
            if failure:return failure
            # One original run (and its durable target rows) exists before the
            # three first claims. start_run is itself transactional/idempotent.
            if gate['run'] is None:service.store.start_run(plan['plan_id'])
            from shared_platform.publication_platform_scheduler import run_native_platform_partitions
            platforms=tuple(p for p in ('TIKTOK','SHOPEE','OZON')
                            if any(t.split(':',1)[0].upper()==p for t in plan['targets']))
            def invoke(platform):
                # ContextVars intentionally do not cross worker threads. Each
                # partition rechecks the same complete stored sole decision.
                part=_NativeExecution(service,decision_id,runtime,platform_scope=(platform,))
                part.plan_id=plan['plan_id'];part_token=_ACTIVE.set(part)
                try:return server._publish_selected_release(data,native_gate=part)
                finally:_ACTIVE.reset(part_token)
            outcomes=run_native_platform_partitions(platforms,invoke,max_parallel=3)
            code=200 if all(c<400 for c,_ in outcomes.values()) else 409
            return code,{'ok':code==200,'native_decision_id':receipt['decision_id'],
                'approval_saved':True,'new_human_review_required':False,
                'platform_results':{p:{'http_status':c,'result':v} for p,(c,v) in outcomes.items()},
                'run':service.store.get_run('release-run:'+plan['payload_digest'][:24])}
        code,result=server._publish_selected_release(data,native_gate=active)
        return code,{**result,'native_decision_id':receipt['decision_id'],
            'approval_saved':True,'new_human_review_required':False}
    finally:_ACTIVE.reset(token)
