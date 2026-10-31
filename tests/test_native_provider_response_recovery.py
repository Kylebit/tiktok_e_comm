"""New explicit task -> real decision/ledger/server/SDK -> original-only READ.

R1/R2/COMMON/source/decision/claims/CAS are real. External provider/config edges
are synthetic. A platform UNLIST read is a HOLD, never publication success.
"""
from copy import deepcopy
import io
import json
from urllib.parse import urlsplit, parse_qs

import pytest

from test_round1_workspace_freeze import live
from test_round1_auto_freeze import public_settings
from owned_new_publication_task import complete_new_task
from modules.products import server
from modules.shopee import client, skill_regions as r
from shared_platform import native_sole_final_execution as execution
from shared_platform import native_new_task_v4_targets as native
from shared_platform import native_sole_final_readback as recovery
from shared_platform.product_publication_executors import build_shopee_region_executor
from domains.product_operations import publication_images_for_target


class Response(io.BytesIO):
    status=200
    headers={'Content-Type':'application/json'}
    def geturl(self):return 'https://example.invalid/provider'


@pytest.mark.parametrize('continue_in_attempt',[False,True],ids=['failed-original-readonly','same-attempt-approved-remainder'])
def test_full_new_native_failure_recovers_original_id_and_strict_readback_without_new_write(live,monkeypatch,tmp_path,continue_in_attempt):
    service_data=tmp_path.parent/('provider-recovery-'+tmp_path.name)
    import test_native_sole_final_service as service_fixture
    from shared_platform.native_windows_actor import NativeActorServiceConfig
    monkeypatch.setattr(service_fixture,'NativeActorServiceConfig',
        lambda _root,operator:NativeActorServiceConfig(service_data,operator))
    runtime,market,task,_=complete_new_task(live,monkeypatch,tmp_path)
    # release_adapters imports this factory by value. Across parameterized
    # fixtures its first import must not retain another fixture's closed DB.
    from modules.products import release_adapters
    from shared_platform.release_store import ReleaseStore
    monkeypatch.setattr(release_adapters,'default_release_store',lambda:ReleaseStore(runtime.store.path))
    wire=[];state={'snapshot':None,'status':'UNLIST','decode_failed':False}
    ctx=r.RegionContext('PH',101,500,'synthetic-shop-token','synthetic-merchant-token')
    token_file=tmp_path/'synthetic-token-binding.json';token_file.write_text('{}')
    from modules.shopee import auth
    from shared_platform import native_canonical_provider_edges as edges
    monkeypatch.setattr(auth,'token_path',lambda:token_file)
    monkeypatch.setattr(edges,'_existing_shopee_context',lambda region,*_:ctx)
    if not continue_in_attempt:
        # Closed process-interruption seam: no in-attempt recovery consumer
        # survives. This exercises only the existing FAILED read-only route.
        from shared_platform import native_provider_continuation as continuation
        monkeypatch.setattr(continuation,'continue_original_result',lambda *args:None)
    monkeypatch.setattr(client,'shopee_config',lambda:{'host':'https://example.invalid','partner_id':123,'partner_key':'synthetic-partner'})
    def parts():
        snapshot=state['snapshot'];label=state['label'];approved=r._approved_models(snapshot,label)
        title,description=r._approved_copy(snapshot,label);images=list(publication_images_for_target(snapshot,label))
        ids=['approved-image-'+str(i) for i in range(len(images))]
        weight,dims=r._parcel_envelope(snapshot)
        item={'item_id':123456789,'item_status':state['status'],'item_sku':r._expected_item_sku(snapshot),
            'has_model':True,'item_name':title,'description':'','description_type':'extended',
            'description_info':{'extended_description':{'field_list':[{'field_type':'text','text':description},
                *[{'field_type':'image','image_info':{'image_id':v}} for v in ids]]}},
            'image':{'image_url_list':images,'image_id_list':ids},
            'category_id':r._expected_category_id(snapshot,label),'weight':weight,
            'dimension':dict(zip(('package_length','package_width','package_height'),dims)),
            'logistic_info':[{'logistic_id':2001,'enabled':True}]}
        stock=snapshot['shopee_global_master']['policy']['stock']['quantity']
        models=[{'model_id':80001+i,'model_sku':v['model_sku'],'tier_index':[i],
            'price_info':[{'currency':v['currency'],'original_price':float(v['price'])}],
            'stock_info_v2':{'seller_stock':[{'location_id':'CNZ','stock':stock}]}}
            for i,v in enumerate(approved)]
        global_models=[{'global_model_sku':v['model_sku'],'tier_index':[i]} for i,v in enumerate(approved)]
        return item,models,global_models
    def network(req,**options):
        path=urlsplit(req.full_url).path;wire.append((req.get_method(),path))
        if req.get_method()=='POST':
            if path=='/api/v2/product/unlist_item':
                assert continue_in_attempt and json.loads(req.data)=={'item_list':[{'item_id':123456789,'unlist':False}]}
                state['status']='NORMAL'
                return Response(b'{"error":"","response":{"success_list":[{"item_id":123456789,"unlist":false}]}}')
            assert path==r.CREATE_TASK_PATH
            return Response(b'{"error":"","response":{"publish_task_id":81234567}}')
        item,models,global_models=parts()
        values={r.TASK_RESULT_PATH:{'publish_status':'success','item_id':123456789},
            '/api/v2/product/get_item_base_info':{'item_list':[item]},
            '/api/v2/product/get_model_list':{'model':models},
            '/api/v2/global_product/get_global_item_id':{'item_id_map':[{'item_id':123456789,'global_item_id':87654321}]},
            '/api/v2/global_product/get_global_model_list':{'global_model':global_models},
            '/api/v2/merchant/get_merchant_warehouse_location_list':[{'location_id':'CNZ','warehouse_name':'中国仓库'}]}
        assert path in values,'only six original official GETs are permitted'
        return Response(json.dumps({'error':'','response':values[path]}).encode())
    monkeypatch.setattr(client,'urlopen_retry',network)
    parse=client._parse_json
    def interrupted(raw,path):
        if path==r.CREATE_TASK_PATH and not state['decode_failed']:
            state['decode_failed']=True
            raise RuntimeError('simulated crash after accepted wire before task parsing')
        return parse(raw,path)
    monkeypatch.setattr(client,'_parse_json',interrupted)
    def dependencies(active,platform,ledger,references,observations):
        assert platform=='SHOPEE'
        class Runtime(r.OfficialShopeeRegionRuntime):
            def context(self,region):return ctx
            def global_models(self,*_):return parts()[2]
            def compatible_logistics(self,*a,**k):return [2001]
            def existing_regional_item(self,*_):return None
            def discover_existing_regional_item(self,*_):return None
            def create_publish_task(self,context,body):
                return ledger.dispatch_reserved('create_publish_task',
                    lambda payload:super(Runtime,self).create_publish_task(context,payload),body)
        class Resolver:
            def __call__(self,request):
                state['snapshot']=deepcopy(request.snapshot);state['label']=request.target_labels[0]
                return '87654321'
            def write_count(self,_request):return 0
        execute=build_shopee_region_executor(global_item_id_resolver=Resolver(),runtime=Runtime())
        def selected(request):
            if request.target_labels==('shopee:PH',):return execute(request)
            return {'schema_version':'product-publication-platform-result/v1','platform':'SHOPEE',
                'targets':[{'target_label':request.target_labels[0],'status':'FAILED'}],
                'dispatch_attempted':False,'readback_completed':False,'external_write_count':0}
        return selected
    monkeypatch.setattr(native,'_dependencies',dependencies)
    # Keep pre-wire diagnostics in this synthetic fixture; production errors
    # remain sanitized and never expose provider/credential text.
    from shared_platform import native_prepared_v4_targets as prepared_targets
    dependency_errors=[]
    original_failure=prepared_targets._pre_submit_failure
    def failure(request,error):
        dependency_errors.append((type(error).__name__,str(error)))
        return original_failure(request,error)
    monkeypatch.setattr(prepared_targets,'_pre_submit_failure',failure)
    try:
        prepared=runtime.prepare(market['plan_id'])
        decision=runtime.decide(nonce=prepared['nonce'],review_digest=prepared['review_digest'])
        with runtime.operation() as service:
            active=execution._NativeExecution(service,decision['decision_id'],runtime,platform_scope=('SHOPEE',))
            active.plan_id=market['plan_id'];token=execution._ACTIVE.set(active)
            try:
                runtime.store.start_run(market['plan_id'])
                code,result=server._publish_selected_release({'offer_id':market['product_id'],
                    'plan_id':market['plan_id'],'confirmation_token':market['confirmation_token'],
                    'confirm_publish':True},native_gate=active)
                assert code==200,result
            finally:execution._ACTIVE.reset(token)
        run_id='release-run:'+market['payload_digest'][:24]
        before=runtime.store.get_run(run_id)
        target=next(t for t in before['targets'] if t['target_label']=='shopee:PH')
        if continue_in_attempt:
            assert target['status']=='SUCCEEDED',(target,dependency_errors)
            proof=target['readback']['evidence']
            assert proof['approved_remaining_steps']['performed']==['LIST_ORIGINAL_ITEM']
            assert proof['approved_remaining_steps']['new_create_calls']==0
            assert proof['approved_remaining_steps']['original_outcome_confirmation_appended'] is True
            assert target['attempts']==1
            with runtime.store._connect_readonly() as db:
                original=db.execute("SELECT r.initial_outcome,o.outcome FROM native_final_budget_reservations r JOIN native_final_budget_outcomes o USING(scope_key,sequence) WHERE r.operation='create_publish_task'").fetchall()
                assert len(original)==1 and tuple(original[0])==('UNKNOWN','ACCEPTED')
            assert sum(method=='POST' and path==r.CREATE_TASK_PATH for method,path in wire)==1
            assert sum(method=='POST' for method,_ in wire)==2
            assert sum(method=='GET' for method,_ in wire)==12
            return
        assert state['decode_failed'] and target['status']=='FAILED',target
        assert target['failure_events'][-1]['evidence']['outcome_unknown'] is True
        with runtime.store._connect_readonly() as db:
            budgets=[tuple(v) for v in db.execute('SELECT * FROM native_final_budget_reservations')]
            outcomes=[tuple(v) for v in db.execute('SELECT * FROM native_final_budget_outcomes')]
        # The accepted task creates an UNLIST item. Recover it, but refuse to
        # pretend that list/copy/media steps were completed by this READ.
        held=recovery.observe_submissions(runtime,decision['decision_id'])
        assert any(v.get('capability')=='ORIGINAL_PROVIDER_RESPONSE_READONLY_HOLD' for v in held['observations']),held
        assert next(t for t in runtime.store.get_run(run_id)['targets'] if t['target_label']==target['target_label'])['status']=='FAILED'
        assert sum(method=='POST' for method,_ in wire)==1
        # A later official NORMAL observation is independently verified. This
        # external fixture fact is not an action performed by the recovery.
        state['status']='NORMAL'
        final=recovery.observe_submissions(runtime,decision['decision_id'])
        current=next(t for t in final['run']['targets'] if t['target_label']==target['target_label'])
        assert current['status']=='SUCCEEDED',final
        assert current['attempts']==target['attempts']==1
        assert current['failure_events']==target['failure_events']
        assert current['readback']['evidence']['historical_unknown_preserved'] is True
        with runtime.store._connect_readonly() as db:
            assert [tuple(v) for v in db.execute('SELECT * FROM native_final_budget_reservations')]==budgets
            assert [tuple(v) for v in db.execute('SELECT * FROM native_final_budget_outcomes')]==outcomes
        assert sum(method=='POST' for method,_ in wire)==1
        assert sum(method=='GET' for method,_ in wire)==12
    finally:runtime.close()
