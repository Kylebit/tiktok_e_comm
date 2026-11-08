"""Real new-task/frozen/ledger/journal -> exact reader -> original FAILED CAS.

Only the SDK network edges are synthetic; no production files, credentials or
database are opened. No publisher/repair is available during reconciliation.
"""
from copy import deepcopy
import hashlib,io,json,socket,sqlite3,urllib.request
import pytest
from test_round1_workspace_freeze import live
from test_round1_auto_freeze import public_settings
from owned_new_publication_task import complete_new_task
from shared_platform import native_sole_final_execution as execution
from shared_platform import native_sole_final_readback as readback


def test_real_failed_publish_readback_requires_full_match_and_preserves_originals(live,monkeypatch,tmp_path):
    from shared_platform.native_final_mutation_budget import build_native_budget_ledger
    from shared_platform.native_prepared_v4_targets import provider_request_for_readonly_recovery,_file_identity
    from shared_platform.r3_frozen_review_producer import read_stored_domain_graph
    from shared_platform.r3_common_source_facts import NativeCommonSourceReader
    from shared_platform.publication_provider_journal import retained_provider_executor
    from shared_platform.product_publication_live_dependencies import TikTokV4DraftCheckpointStore
    from modules.miaoshou.tiktok_v4_drafts import DraftWriteFact
    from modules.miaoshou.tiktok_publisher import EXPECTED_SHOP_ID_BY_TARGET,PUBLISH_PATH
    from domains.channel_operations.tiktok_v4_execution import _resolved_category,_target_command
    from shared_platform.native_tiktok_exact_readback import MIAOSHOU_SHOPS,SHOP_PATH,SEARCH,DETAIL
    from shared_platform.product_description_media import miaoshou_rich_description
    from modules.miaoshou import client
    from core import auth,api_client
    import test_native_sole_final_service as service_fixture
    from shared_platform.native_windows_actor import NativeActorServiceConfig
    service_data=tmp_path.parent/('tk-failed-service-'+tmp_path.name)
    monkeypatch.setattr(service_fixture,'NativeActorServiceConfig',lambda _root,operator:NativeActorServiceConfig(service_data,operator))
    label='tiktok:LH_PH';internal=str(EXPECTED_SHOP_ID_BY_TARGET[label]);detail_id='73001'
    runtime,market,task,_=complete_new_task(live,monkeypatch,tmp_path)
    wire=[];state={'mode':'match'}
    def forbidden(*args,**kwargs):pytest.fail('external network is forbidden')
    monkeypatch.setattr(socket.socket,'connect',forbidden)
    try:
        prepared=runtime.prepare(market['plan_id'])
        decision=runtime.decide(nonce=prepared['nonce'],review_digest=prepared['review_digest'])
        with runtime.operation() as service:
            active=execution._NativeExecution(service,decision['decision_id'],runtime,platform_scope=('TIKTOK',))
            active.plan_id=market['plan_id'];token=execution._ACTIVE.set(active)
            try:
                run=runtime.store.start_run(market['plan_id']);rid=run['run_id']
                runtime.store.begin_target(rid,label)
                with runtime.store._connect_readonly() as db:
                    db.execute('BEGIN')
                    graph=read_stored_domain_graph(db,market['plan_id'],native_reader=NativeCommonSourceReader(runtime.store))
                    snapshot=json.loads(db.execute('SELECT snapshot_json FROM approved_publication_snapshots WHERE plan_id=?',(market['plan_id'],)).fetchone()[0])
                ledger=build_native_budget_ledger(active,platform='TIKTOK',target_label=label,snapshot=snapshot,candidate=json.loads(graph.display_bytes))
                target=next(x for x in runtime.store.get_run(rid)['targets'] if x['target_label']==label)
                request=provider_request_for_readonly_recovery(active,market,target,snapshot)
                checkpoint=TikTokV4DraftCheckpointStore(runtime.supervisor.root)
                for operation in ('CREATE_DRAFT','CLAIM_TO_SHOP','SAVE_DRAFT'):
                    checkpoint.record_fact(request,label,DraftWriteFact(operation,'ACCEPTED',detail_id,internal))
                body={'detailIds':[int(detail_id)],'shopIds':[internal]}
                class Response(io.BytesIO):
                    status=200;headers={'Content-Type':'application/json'}
                    def geturl(self):return 'https://example.invalid'+PUBLISH_PATH
                def accepted(req,**kwargs):
                    assert req.get_method()=='POST' and req.full_url.endswith(PUBLISH_PATH) and json.loads(req.data)==body
                    wire.append(('owned-original-publish',PUBLISH_PATH));return Response(b'{"result":"success","data":null}')
                monkeypatch.setattr(urllib.request,'urlopen',accepted)
                ledger.reserve_target(label,'publish_target')
                execute=retained_provider_executor(lambda _:ledger.dispatch_reserved('publish_target',
                    lambda path,payload:client.post_open(path,payload,cfg={'app_id':'owned','app_secret':'synthetic'},base_url='https://example.invalid'),PUBLISH_PATH,body))
                assert execute(request)['result']=='success';ledger.complete_reserved('publish_target','ACCEPTED')
                original=next(x for x in snapshot['publication_targets'] if x['target_label']==label)
                category,digest=_resolved_category(snapshot['categories_by_target'][label],target=original,product=snapshot['product'],skus=snapshot['skus'],resolver=None)
                command=_target_command(snapshot,target=original,control={'detail_id':detail_id,'shop_id':internal},category=category,category_evidence_digest=digest)
                expected=command['publisher_snapshot']['targets'][0]
                inventory={sku['model_sku']:{str(w['warehouse_id']):w['stock'] for w in expected['expected_warehouse_inventory']['warehouses']} for sku in command['skus']}
                failure={'schema_version':'native-scoped-v4-target-result/v1','target_label':label,'status':'FAILED','verified':False,
                    'outcome_unknown':False,'known_pre_submit_zero':False,'reconciliation_required':False,'external_writes_performed':[label],
                    'checkpoint_file_identity':_file_identity(run_id=rid,plan_id=market['plan_id'],decision_id=decision['decision_id'],snapshot_digest=snapshot['snapshot_digest']),
                    'provider_readonly_observations':{'tiktok_prewrite_inventory':{label:inventory},'tiktok_official':{}},
                    'canonical_platform_result':{'platform':'TIKTOK','dispatch_attempted':True,'targets':[{'target_label':label,'status':'FAILED','evidence':{
                        'stage':'PUBLISH','provider_code':'success','request_attempted':True,'outcome_unknown':False,'external_write_count':1,'status':'FAILED'}}]}}
                runtime.store.record_target_failure(rid,label,error='exact official storefront not found yet',failure_evidence=failure)
            finally:execution._ACTIVE.reset(token)
        token_path=tmp_path/'owned-read-token.json';token_path.write_text('{"access_token":"synthetic-read-only"}')
        monkeypatch.setattr(auth,'token_path',lambda:token_path)
        monkeypatch.setattr(auth,'is_access_expired',lambda *args,**kwargs:False)
        monkeypatch.setattr(auth,'refresh_token',forbidden,raising=False)
        def shop_post(path,body,**kwargs):
            assert path==MIAOSHOU_SHOPS and body=={'platform':'TIKTOK','site':'PH','pageNo':1,'pageSize':100}
            wire.append(('erp-read',path));row={'shopId':internal,'shopName':'Original Store','site':'PH','platform':'TIKTOK','platformShopId':'911','tiktokShopId':'911'}
            if state['mode']=='explicit-conflict':row['tiktokShopId']='different-official-shop'
            if state['mode']=='country-conflict':row['site']='MY'
            return {'result':'success','data':{'list':[row],'total':2 if state['mode']=='erp-partial' else 1}}
        monkeypatch.setattr(client,'post_open',shop_post)
        detail={'id':'301','title':expected['expected_title'],'description':miaoshou_rich_description(expected['expected_description'],expected['expected_images']),
            'status':'ACTIVATE','category_id':expected['expected_category_id'],'package_weight':{'unit':'KG','value':expected['expected_weight_kg']},
            'package_dimensions':dict(zip(('length','width','height'),expected['expected_package_cm']),unit='CM'),
            'main_images':[{'urls':[image]} for image in expected['expected_images']],
            'skus':[{'id':str(500+i),'seller_sku':sku['model_sku'],'price':{'sale_price':sku['price'],'currency':sku['currency']},
                'sales_attributes':[{'name':key,'value_name':value} for key,value in sku['specification'].items()],
                'inventory':[{'warehouse_id':key,'quantity':value} for key,value in inventory[sku['model_sku']].items()]} for i,sku in enumerate(command['skus'],1)]}
        def official(method,path,access,query=None,body=None,*args,**kwargs):
            wire.append((method,path));assert access=='synthetic-read-only'
            if path==SHOP_PATH:return {'code':1 if state['mode']=='auth-nonzero' else 0,'data':{'shops':[{'id':'911','name':'Original Store','region':'PH','cipher':'exact-original-cipher'}]}}
            if path==SEARCH:
                assert method=='POST' and body=={'status':'ACTIVATE','seller_skus':[x['model_sku'] for x in command['skus']]}
                return {'code':0,'data':{'products':[{'id':'301','skus':detail['skus']}],'total_count':1,'next_page_token':''}}
            assert method=='GET' and path==DETAIL.format(product_id='301') and query=={'shop_cipher':'exact-original-cipher'}
            value=deepcopy(detail)
            if state['mode']=='parcel-missing':value.pop('package_dimensions')
            if state['mode']=='copy-mismatch':value['title']='different title'
            if state['mode']=='media-missing':value['description']=expected['expected_description']
            if state['mode']=='stock-mismatch':value['skus'][0]['inventory'][0]['quantity']+=1
            if state['mode']=='gallery-mismatch':value['main_images'].reverse()
            if state['mode']=='price-mismatch':value['skus'][0]['price']['sale_price']='0.01'
            if state['mode']=='sku-mismatch':value['skus'][0]['seller_sku']='unapproved-model'
            if state['mode']=='cas-drift':
                with runtime.store._connect() as db:
                    db.execute('UPDATE release_target_runs SET error=? WHERE run_id=? AND target_label=?',('owned concurrent state change',rid,label))
            return {'code':0,'data':value}
        monkeypatch.setattr(api_client,'_do_request_once',official)
        before=next(x for x in runtime.store.get_run(rid)['targets'] if x['target_label']==label)
        with runtime.store._connect_readonly() as db:
            preserved={name:[tuple(row) for row in db.execute('SELECT * FROM '+name)] for name in
                ('native_final_budget_scopes','native_final_budget_reservations','native_final_budget_dispatches','native_final_budget_outcomes','release_target_failure_events','release_target_submissions')}
        # Same consumer and full reader: each partial/conflicting official
        # response remains FAILED and cannot cause another provider mutation.
        for mode in ('explicit-conflict','country-conflict','auth-nonzero','erp-partial','parcel-missing','copy-mismatch','media-missing',
                'stock-mismatch','gallery-mismatch','price-mismatch','sku-mismatch','cas-drift'):
            state['mode']=mode
            held=readback.observe_submissions(runtime,decision['decision_id'])
            assert next(x for x in runtime.store.get_run(rid)['targets'] if x['target_label']==label)['status']=='FAILED',held
            assert not next(x for x in runtime.store.get_run(rid)['targets'] if x['target_label']==label)['readback']
            if mode=='cas-drift':
                with runtime.store._connect() as db:
                    db.execute('UPDATE release_target_runs SET error=? WHERE run_id=? AND target_label=?',(before['error'],rid,label))
        # An absent retained response stays UNKNOWN even when another local
        # budget outcome says ACCEPTED. It cannot authorize a READ/CAS/replay.
        from shared_platform.publication_provider_journal import ProviderResponseJournal,_scope
        journal_root,journal_identity=_scope(request);journal=ProviderResponseJournal(journal_root,journal_identity)
        with sqlite3.connect(journal.path) as db:
            original_response=db.execute('SELECT key,phase,response_body FROM provider_response_intents').fetchone()
            db.execute('UPDATE provider_response_intents SET phase=?,response_body=NULL WHERE key=?',('INTENT',original_response[0]))
        state['mode']='match';count=len(wire)
        held=readback.observe_submissions(runtime,decision['decision_id'])
        assert len(wire)==count and not next(x for x in runtime.store.get_run(rid)['targets'] if x['target_label']==label)['readback'],held
        with sqlite3.connect(journal.path) as db:
            db.execute('UPDATE provider_response_intents SET phase=?,response_body=? WHERE key=?',(original_response[1],original_response[2],original_response[0]))
        state['mode']='match'
        result=readback.observe_submissions(runtime,decision['decision_id'])
        after=next(x for x in result['run']['targets'] if x['target_label']==label)
        assert after['status']=='SUCCEEDED',result
        assert after['attempts']==before['attempts'] and after['failure_events']==before['failure_events']
        assert not after['submission'] and after['readback']['evidence']['verified'] is True
        assert len(after['readback']['evidence']['official_catalog_rows'])==3
        from shared_platform.native_companion_promotions import canonical_prerequisite
        prerequisite=canonical_prerequisite(after,label)
        assert prerequisite['external_id']==after['external_id']=='301'
        with runtime.store._connect_readonly() as db:
            assert {name:[tuple(row) for row in db.execute('SELECT * FROM '+name)] for name in preserved}==preserved
        assert sum(kind=='owned-original-publish' for kind,path in wire)==1
        assert all(path in {PUBLISH_PATH,MIAOSHOU_SHOPS,SHOP_PATH,SEARCH,DETAIL.format(product_id='301')} for kind,path in wire)
        count=len(wire)
        for selected in ((label,label),('tiktok:UNKNOWN_SHOP',)):
            with pytest.raises((ValueError,RuntimeError),match='EXACT_SELECTED_READBACK_TARGETS_REQUIRED'):
                readback.observe_submissions(runtime,decision['decision_id'],target_labels=selected)
        unchanged=readback.observe_submissions(runtime,decision['decision_id'],target_labels=(label,))
        assert len(wire)==count and next(x for x in unchanged['run']['targets'] if x['target_label']==label)==after
    finally:runtime.close()
