"""Startup-owned R3 adapter for one signed Agent-prepared adoption.

It delegates baseline EDIT, full marketplace candidate and sole final decision
to the existing native domain service. CREATE has its own irreversible cap1.
"""
from contextlib import contextmanager
import hmac,json,sqlite3
from pathlib import Path
from copy import deepcopy
from decimal import Decimal,InvalidOperation
from shared_platform import agent_prepared_publication_adoption as adoption
from shared_platform.manual_common_creation import ManualCommonCreateLedger,ManualCommonBlocked,CREATE_PATH,validate_create_body

def frozen_create_body(product,seller_sku):
    """COMMON technical seed is the frozen CNY cost, never a shop sale price."""
    try:
        cost=Decimal(str(product['cost_cny']))
        rows=[x for x in product['skus'] if x['seller_sku']==seller_sku]
        if not cost.is_finite() or cost<=0 or len(rows)!=1 or Decimal(str(rows[0]['cost_cny']))!=cost:
            raise ValueError()
        return validate_create_body(dict(title=product['title'],itemNum=seller_sku,price=float(cost)))
    except (ValueError,KeyError,InvalidOperation,OverflowError):
        raise ManualCommonBlocked('MANUAL_COMMON_FROZEN_CNY_COST_SEED_REQUIRED')

SCOPE='agent-prepared-publication/v1'
PREPARATION_POST_PATHS=frozenset({
 '/api/product-workspace/r3-common/preview','/api/product-workspace/miaoshou-draft/commit',
 '/api/product-workspace/r3-marketplace/preview'})

class AgentPreparedCommonService:
    def __init__(self,final,task_id,*,catalog_path=None,successor_decision=None,correction_decision=None,baseline_decision=None,colormap_decision=None):
        from shared_platform.native_sole_final_service import NativeSoleFinalService
        if type(final) is not NativeSoleFinalService or not final._new_decision_execution_enabled:
            raise adoption.AdoptionBlocked('ADOPTION_NATIVE_FINAL_SERVICE_REQUIRED')
        self.final=final;self.task_id=task_id;self.operations=final._operations
        self.store=final.store;self.profile=self.operations.profile;self.engine=self.operations.engine
        if catalog_path is not None and not Path(catalog_path).is_absolute():
            raise adoption.AdoptionBlocked('ADOPTION_ABSOLUTE_CATALOG_WRITER_REQUIRED')
        self.catalog_path=Path(catalog_path).resolve(strict=True) if catalog_path is not None else None
        self.ledger=ManualCommonCreateLedger(self.store.path)
        from shared_platform.manual_common_successor import ManualCommonSuccessorLedger
        self.successor=ManualCommonSuccessorLedger(self.store.path,decision_reader=successor_decision) if successor_decision is not None else None
        from shared_platform.manual_common_successor import ManualCommonCorrectionLedger
        self.correction=ManualCommonCorrectionLedger(self.store.path,decision_reader=correction_decision) if correction_decision is not None else None
        self.baseline_decision=baseline_decision
        self.colormap_decision=colormap_decision
        self._current()
    def _select_intent(self,task,record):
        if getattr(self,'baseline_decision',None) is not None:
            from shared_platform.manual_common_successor import effective_child
            row=effective_child(self.store.path,record['offer_id'])
            if row is None or row['state']!='VERIFIED' or row['detail_id']!=self.baseline_decision.read()['common_detail_id']:
                raise ManualCommonBlocked('COMMON_BASELINE_SUCCESSOR_VERIFIED_CREATE_REQUIRED')
            return self.correction,row  # No reserve/recreate under the new v4.
        row=self.ledger.reserve(self._intent(task,record))
        if row['state']=='UNKNOWN' and self.correction is not None:
            from shared_platform.manual_common_successor import stored_child
            child=stored_child(self.store.path,record['offer_id'])
            product=record['facts']['preparation_source']['round1_snapshot']['fact_snapshot']['product_facts']
            return self.correction,self.correction.reserve_correction(child,task=task,adoption_record=record,
                source_chain_digest=self._source_chain(task),create_body=frozen_create_body(product,record['facts']['seller_sku']))
        if row['state']=='UNKNOWN' and self.successor is not None:
            return self.successor,self.successor.reserve_successor(row,task=task,adoption_record=record,
                owner_sid=record['owner_sid'],source_chain_digest=self._source_chain(task))
        return self.ledger,row
    def _source_chain(self,task):
        # _current() has independently verified this signed v2 against the
        # same Task, owner HMAC, original adoption and actual runtime source.
        path=Path(self.profile.data_root)/'tasks.db'
        correction=self.correction is not None
        table='agent_prepared_source_successors_v3' if correction else 'agent_prepared_source_successors_v2'
        with sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True) as db:
            row=db.execute(f'SELECT record_json,record_digest FROM {table} WHERE task_id=?',(task['task_id'],)).fetchone()
        if row is None:raise ManualCommonBlocked('COMMON_SUCCESSOR_SIGNED_SOURCE_CHAIN_REQUIRED')
        record=json.loads(row[0]);reader=(self.correction if correction else self.successor).decision_reader
        if (record['source']['new_version']!=task['version'] or record['decision_sha256']!=reader.sha256
            or record['decision_path']!=str(reader.path)):raise ManualCommonBlocked('COMMON_SUCCESSOR_SIGNED_SOURCE_CHAIN_CHANGED')
        return row[1]
    def _current(self):
        from shared_platform import workbench_publication_native as native
        task=self.engine.get(self.task_id);frozen=native.read_frozen(task,self.profile)
        if not frozen:raise adoption.AdoptionBlocked('ADOPTION_CURRENT_NATIVE_R1_REQUIRED')
        record=adoption.verify(task,self.profile,offer=frozen['offer_id'],targets=frozen['targets'],
            prepared_reference=frozen['prepared_reference'],snapshot_digest=frozen['snapshot_digest'])
        images=native.read_images(task,self.profile)
        if images['native_r2']!=record['facts']['r2_identity']:raise adoption.AdoptionBlocked('ADOPTION_CURRENT_R2_CHANGED')
        return task,record
    def owns(self,offer):return self._current()[0]['scope']['offer_id']==offer
    def _actor(self):
        with self.final.operation() as service:actor=service._actor()
        return actor
    def _token(self,intent_id):
        _,key=adoption._owner_key(self.profile)
        return hmac.new(key,('technical-common:'+intent_id).encode(),'sha256').hexdigest()
    def _post(self,path,body):
        from modules.products import server
        from modules.miaoshou import client
        from shared_platform.publication_runtime_config import common_signing_bytes
        before=common_signing_bytes(server.R3_STARTUP_CONFIG)
        cfg=json.loads(before.decode('utf-8-sig'))
        if cfg.get('base_url',client.OPEN_BASE_URL).rstrip('/')!=client.OPEN_BASE_URL:
            raise ManualCommonBlocked('MANUAL_COMMON_OFFICIAL_ENDPOINT_REQUIRED')
        if common_signing_bytes(server.R3_STARTUP_CONFIG)!=before:raise ManualCommonBlocked('MANUAL_COMMON_CONFIG_CHANGED')
        return client.post_open(path,body,cfg=cfg)
    def _detail(self,identity):
        reply=self._post('/open/v1/product/common_collect_box/common_collect_box/get_common_collect_box_detail',
            {'commonCollectBoxDetailId':int(identity)})
        if reply.get('result')!='success':raise ManualCommonBlocked('MANUAL_COMMON_DETAIL_UNAVAILABLE')
        detail=(reply.get('data') or {}).get('editCommonCollectBoxDetail')
        if type(detail) is not dict:raise ManualCommonBlocked('MANUAL_COMMON_DETAIL_UNAVAILABLE')
        return detail
    def _scan_common(self,seller_sku):
        """Complete bounded official pagination; no sourceItemId is fabricated."""
        result=[];seen=set()
        for page in range(1,101):
            reply=self._post('/open/v1/product/common_collect_box/common_collect_box/get_common_collect_box_list',
                {'pageNo':page,'pageSize':100,'filter':{'tabPaneName':'all'}})
            data=reply.get('data') or {};rows=data.get('detailList')
            if reply.get('result')!='success' or type(rows) is not list:raise ManualCommonBlocked('MANUAL_COMMON_LIST_INCOMPLETE')
            for row in rows:
                identity=str(row.get('commonCollectBoxDetailId') or row.get('id') or '')
                if not identity or identity in seen:raise ManualCommonBlocked('MANUAL_COMMON_LIST_IDENTITY_AMBIGUOUS')
                seen.add(identity)
                if str(row.get('itemNum') or '')==seller_sku:result.append(self._detail(identity))
            total=data.get('total') if 'total' in data else data.get('totalCount')
            if type(total) is not int or total<0:raise ManualCommonBlocked('MANUAL_COMMON_LIST_TOTAL_REQUIRED')
            if len(seen)>=total:
                if len(seen)!=total:raise ManualCommonBlocked('MANUAL_COMMON_LIST_TOTAL_CHANGED')
                return result
            if not rows:raise ManualCommonBlocked('MANUAL_COMMON_LIST_INCOMPLETE')
        raise ManualCommonBlocked('MANUAL_COMMON_LIST_PAGINATION_LIMIT')
    def _intent(self,task,record):
        from modules.products import server
        from shared_platform.publication_common_standing_policy import inspect_service_policy
        policy=inspect_service_policy(server._service_common_standing_policy_reader())
        if policy.status!='KNOWN_LOCAL_USER_INTENT' or policy.maximum_confirmed_writes!=1:
            raise ManualCommonBlocked('MANUAL_COMMON_STANDING_PREPARATION_AUTHORITY_REQUIRED')
        facts=record['facts'];source=facts['preparation_source'];r1=source['round1_snapshot']
        product=r1['fact_snapshot']['product_facts']
        body=frozen_create_body(product,facts['seller_sku'])
        existing=self.ledger.original_for_offer(record['offer_id'])
        if existing is not None:
            old=existing['intent']['create_body']
            if old.get('title')!=body['title'] or old.get('itemNum')!=body['itemNum']:
                raise ManualCommonBlocked('MANUAL_COMMON_HISTORICAL_BODY_CHANGED')
            body=deepcopy(old)  # Audit/recovery uses the exact historical body.
        return dict(schema_version='manual-common-create-intent/v1',offer_id=record['offer_id'],
          preparation_root_id=source['preparation_root']['root_id'],round1_digest=facts['snapshot_digest'],
          r2_digest='sha256:'+adoption.sha256(adoption._bytes(facts['r2_identity'])).hexdigest(),
          source_identity_binding_sha256=facts['source_identity_binding_sha256'],owner_sid=record['owner_sid'],
          seller_sku=facts['seller_sku'],model_skus=sorted(facts['aliases'].values()),target_labels=record['targets'],
          forbidden_detail_ids=[record['offer_id']],create_body=body,
          authority='CURRENT_NEW_PRODUCT_PREPARATION',maximum_create_attempts=1,maximum_baseline_edits=1)
    def preview(self,data):
        task,record=self._current();self._actor()
        if data.get('offer_id')!=record['offer_id']:raise ManualCommonBlocked('MANUAL_COMMON_REQUEST_SCOPE_CHANGED')
        ledger,row=self._select_intent(task,record)
        if row['state']=='VERIFIED':
            from modules.products import server
            return server._preview_r3_common_stage(data,_persist_native_plan=False,_manual_delegate=False)
        return 200,{'ok':True,'offer_id':record['offer_id'],'schema_version':'publication-stages/v1',
            'common':{'status':'RECONCILIATION_REQUIRED' if row['state']=='UNKNOWN' else 'READY_TO_CREATE',
              'execution_scope':['miaoshou:COMMON'],'plan':{'plan_id':row['intent_id'],
                'confirmation_token':self._token(row['intent_id']),'payload_digest':row['intent_digest']},
              'create_attempts':row['attempts'],'maximum_create_attempts':1,'combined_create_attempt_cap':3 if ledger is self.correction else (2 if ledger is self.successor else 1),'maximum_baseline_edits':1,
              'new_common_human_approval_needed':False},'external_writes_performed':[]}
    def commit(self,data):
        task,record=self._current();self._actor()
        if (data.get('offer_id')!=record['offer_id'] or data.get('release_stage')!='R3_COMMON'
            or data.get('publication_targets')!=['miaoshou:COMMON'] or data.get('confirm_miaoshou_write') is not True):
            raise ManualCommonBlocked('MANUAL_COMMON_REQUEST_SCOPE_CHANGED')
        ledger,row=self._select_intent(task,record)
        if getattr(self,'baseline_decision',None) is not None:
            return self._commit_baseline_successor(data,task,record)
        if data.get('plan_id')!=row['intent_id'] or not hmac.compare_digest(str(data.get('confirmation_token') or ''),self._token(row['intent_id'])):
            raise ManualCommonBlocked('MANUAL_COMMON_EXACT_TECHNICAL_PLAN_REQUIRED')
        if row['state']=='RESERVED':
            existing=self._scan_common(row['intent']['seller_sku'])
            if existing:
                if ledger in (self.successor,self.correction):
                    observation=ledger.observe_existing(row['intent_id'],existing)
                    raise ManualCommonBlocked('MANUAL_COMMON_EXISTING_PRODUCT_REQUIRES_RECONCILIATION:'+','.join(observation['detail_ids']))
                raise ManualCommonBlocked('MANUAL_COMMON_EXISTING_PRODUCT_REQUIRES_RECONCILIATION')
            row=ledger.create(row['intent_id'],post_create=lambda body:self._post(CREATE_PATH,body),read_detail=self._detail)
        elif row['state']=='UNKNOWN':
            # Same original intent, read-only recovery, never another CREATE.
            if row['create_response'] is not None:
                row=ledger.reconcile_returned(row['intent_id'],read_detail=self._detail)
            else:
                row=ledger.reconcile(row['intent_id'],candidates=self._scan_common(row['intent']['seller_sku']),complete=True)
        from modules.products import server
        from shared_platform import operations_publication_common as common
        request={'offer_id':record['offer_id'],'release_stage':'R3_COMMON','publication_targets':['miaoshou:COMMON']}
        code,view=server._preview_r3_common_stage(request,_persist_native_plan=False,_manual_delegate=False)
        if code!=200:return code,view
        plan=view['common']['plan'];worker='agent-prepared-common'
        self.engine.register_executor(worker,['publication'],self.engine.release)
        latest=self.engine.get(self.task_id)
        if latest['execution_state']=='waiting_user' and view['common']['status']=='VERIFIED':
            return server._publication_stages_for_request({'offer_id':record['offer_id']})
        if latest['execution_state']=='waiting_domain':
            # An explicit continuation may reconcile the original durable EDIT;
            # the original ledger refuses transport replay for UNKNOWN.
            original=self.store.get_plan(plan['plan_id'])
            if original:
                code,recovery=server._prepare_native_common_ledger({'offer_id':record['offer_id'],
                    'plan_id':plan['plan_id'],'confirm_miaoshou_write':True},self.store)
                if code!=200:return code,recovery
            common.observe(self.engine,latest,self.profile,server_module=server)
            latest=self.engine.get(self.task_id)
        if latest['execution_state']!='queued':
            raise ManualCommonBlocked('MANUAL_COMMON_ACTIVE_TASK_OWNER_REQUIRES_RECONCILIATION')
        claimed=self.engine.claim(self.task_id,worker,ttl=300)
        if claimed is None:raise ManualCommonBlocked('MANUAL_COMMON_TASK_LEASE_REQUIRED')
        lease=claimed['lease_token'];task=self.engine.get(self.task_id)
        complete=common.run(self.engine,task,lease,self.profile,server_module=server)
        if complete:
            from shared_platform.workbench_publication_native import read_frozen
            self.engine.wait_for_user(self.task_id,lease,kind='review',label='审核最终发布包',
                reason='核对完整冻结商品、三个规格与准确店铺范围后，在可信终审入口提交一次发布决定。',
                url='/product-workspace?offer_id='+record['offer_id']+'&round=final',
                receipt_binding={'adapter':'publication-native/v1','step':'release','native_r1':read_frozen(task,self.profile)})
        return server._publication_stages_for_request({'offer_id':record['offer_id']})

    def _commit_baseline_successor(self,data,task,record):
        """Use the original protected commit, managed SDK and final materializer."""
        from modules.products import server
        from shared_platform import operations_publication_common as common
        from shared_platform.native_common_baseline_successor import context
        request={'offer_id':record['offer_id'],'release_stage':'R3_COMMON','publication_targets':['miaoshou:COMMON']}
        code,view=server._preview_r3_common_stage(request,_persist_native_plan=False,_manual_delegate=False)
        if code!=200:return code,view
        plan=view['common']['plan']
        if data['plan_id']!=plan['plan_id'] or not hmac.compare_digest(str(data.get('confirmation_token') or ''),plan['confirmation_token']):
            raise ManualCommonBlocked('COMMON_BASELINE_SUCCESSOR_EXACT_TECHNICAL_PLAN_REQUIRED')
        execution_reader=getattr(self,'colormap_decision',None) or self.baseline_decision
        decision=execution_reader.read()
        with self.store._connect_readonly() as db:
            db.execute('BEGIN');context(self.store,db,plan['payload'])
            existing=db.execute('SELECT technical_execution_state FROM release_runs WHERE plan_id=?',(plan['plan_id'],)).fetchone()
        if existing is not None and existing['technical_execution_state']=='UNKNOWN':
            # An accepted reply permits only the original run's READ recovery.
            # Missing acceptance returns UNKNOWN here, before task/lease changes.
            code,result=server._prepare_native_common_ledger({'offer_id':record['offer_id'],
                'plan_id':plan['plan_id'],'confirm_miaoshou_write':True},self.store)
            if code!=200:return code,result
        latest=self.engine.get(self.task_id)
        if latest['execution_state']=='waiting_user':return server._publication_stages_for_request({'offer_id':record['offer_id']})
        worker='agent-prepared-common';self.engine.register_executor(worker,['publication'],self.engine.release)
        latest=self.engine.get(self.task_id)
        if latest['execution_state']=='waiting_domain':
            action=latest.get('pending_observation') or {}
            receipt=dict(receipt_id='baseline-successor-ready:'+execution_reader.sha256+':'+str(action.get('action_id')),action_id=action.get('action_id'))
            current,_=self._current()
            with self.store._connect_readonly() as db:
                db.execute('BEGIN');context(self.store,db,plan['payload'])
            if current!=latest:raise ManualCommonBlocked('DOMAIN_RECEIPT_CURRENT_TASK_CHANGED')
            def verify(value,binding):
                return (value==receipt and current['execution_state']=='waiting_domain' and action.get('kind')=='observe'
                    and binding.get('adapter')==common.ADAPTER and binding.get('offer_id')==record['offer_id']
                    and ((binding.get('plan_id')==decision['original_plan_id'] and binding.get('payload_digest')==decision['original_payload_digest'])
                         or (binding.get('plan_id')==decision.get('rejected_plan_id') and binding.get('payload_digest')==decision.get('rejected_payload_digest'))
                         or (binding.get('plan_id')==plan['plan_id'] and binding.get('payload_digest')==plan['payload_digest'])))
            self.engine.accept_domain_receipt(self.task_id,receipt,verify)
            latest=self.engine.get(self.task_id)
        if latest['execution_state']!='queued':raise ManualCommonBlocked('MANUAL_COMMON_ACTIVE_TASK_OWNER_REQUIRES_RECONCILIATION')
        claimed=self.engine.claim(self.task_id,worker,ttl=300)
        if claimed is None:raise ManualCommonBlocked('MANUAL_COMMON_TASK_LEASE_REQUIRED')
        lease=claimed['lease_token']
        try:
            reserved=self.store.create_plan(plan['payload'])
            if reserved['plan_id']!=plan['plan_id'] or reserved['payload_digest']!=plan['payload_digest']:
                raise ManualCommonBlocked('COMMON_BASELINE_SUCCESSOR_FROZEN_PAYLOAD_CHANGED')
            code,result=server._prepare_native_common_ledger({'offer_id':record['offer_id'],
                'plan_id':plan['plan_id'],'confirm_miaoshou_write':True},self.store)
            current=self.engine.get(self.task_id)
            if code!=200:
                common._block_missing_common_authority(self.engine,current,lease,dict(status='BLOCKED',
                    binding=common._verified_binding(current,view['common'],self.profile),
                    blockers=[result.get('error') or 'COMMON_BASELINE_SUCCESSOR_OUTCOME_UNKNOWN']))
                return code,result
            after=common._read(server,current)
            complete=common._materialize_or_block(self.engine,server,current,lease,after,self.profile)
            if complete:
                from shared_platform.workbench_publication_native import read_frozen
                self.engine.wait_for_user(self.task_id,lease,kind='review',label='审核最终发布包',
                    reason='核对完整冻结商品、三个规格与准确店铺范围后，在可信终审入口提交一次发布决定。',
                    url='/product-workspace?offer_id='+record['offer_id']+'&round=final',
                    receipt_binding={'adapter':'publication-native/v1','step':'release','native_r1':read_frozen(current,self.profile)})
            return server._publication_stages_for_request({'offer_id':record['offer_id']})
        except Exception:
            # Leave the durable native run/attempt untouched. The task lease
            # cannot authorize a second request or hide an unresolved successor.
            self.engine.observe_reconciliation(self.task_id,lease,'COMMON successor需要对账，保留原与新操作，不重复EDIT')
            raise

def admit_preparation_request(http,path,data):
    service=getattr(http,'agent_prepared_common',None)
    if type(service) is not AgentPreparedCommonService or path not in PREPARATION_POST_PATHS or not service.owns(data.get('offer_id')):
        raise ManualCommonBlocked('MANUAL_COMMON_SERVICE_SCOPE_REQUIRED')
    allowed={'offer_id'} if path.endswith('/r3-marketplace/preview') else {'offer_id','release_stage','publication_targets'}
    if path.endswith('/miaoshou-draft/commit'):allowed|={'plan_id','confirmation_token','confirm_miaoshou_write'}
    if set(data)!=allowed:raise ManualCommonBlocked('MANUAL_COMMON_REQUEST_FIELDS_INVALID')
    service._actor()
    return data

@contextmanager
def installed_agent_prepared_service(application,http,operations,config):
    """Only startup configuration selects the one genuine adopted task.

    No new-task worker, historical scan, schema initialization or HTTP receipt
    import is installed. The ordinary native actor/nonce decision stays intact.
    """
    from shared_platform.native_service_lifecycle import installed_native_services
    task_id=config.get('agent_prepared_task_id');expected=config.get('agent_prepared_adoption_digest')
    if type(task_id) is not str or not task_id.startswith('TASK-') or type(expected) is not str or len(expected)!=64:
        raise adoption.AdoptionBlocked('ADOPTION_PINNED_STARTUP_TASK_REQUIRED')
    previous=getattr(application,'_AGENT_PREPARED_COMMON_SERVICE',None)
    with installed_native_services(application,http,operations,install_new_tasks=False) as installed:
        from shared_platform.manual_common_successor import installed_decision,installed_correction_decision
        from shared_platform.native_common_baseline_successor import installed_decision as installed_baseline_decision
        from shared_platform.native_common_colormap_correction import installed_decision as installed_colormap_decision
        service=AgentPreparedCommonService(installed['final'],task_id,catalog_path=config.get('agent_prepared_catalog_database'),
            successor_decision=installed_decision(config),correction_decision=installed_correction_decision(config),
            baseline_decision=installed_baseline_decision(config),colormap_decision=installed_colormap_decision(config))
        task,record=service._current()
        if adoption.sha256(adoption._bytes(record)).hexdigest()!=expected:
            raise adoption.AdoptionBlocked('ADOPTION_PINNED_STARTUP_TASK_CHANGED')
        application._AGENT_PREPARED_COMMON_SERVICE=service
        http.agent_prepared_common=service;operations.agent_prepared_common=service
        from shared_platform import prepared_target_consumer as target_scope
        prior_target_consumer=target_scope._INSTALLED
        api_consumer=target_scope.completed_api_consumer(service)
        if api_consumer is not None:target_scope.install_prepared_target_consumer(api_consumer)
        from shared_platform.publication_ozon_card_structure import installed_structure
        try:
            with installed_structure(service,config):
                yield service
        finally:
            target_scope._INSTALLED=prior_target_consumer
            application._AGENT_PREPARED_COMMON_SERVICE=previous
            del http.agent_prepared_common;del operations.agent_prepared_common
