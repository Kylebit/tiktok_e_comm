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
    # The ORIGINAL source freezes main=200 and secondary=0. The consumer must
    # prove the latter using the authoritative all-warehouse total, never an
    # omitted-detail-row default.
    import owned_new_publication_task as owned
    original_allocations=owned.owned_warehouse_allocations
    def allocations(labels):
        rows=original_allocations(labels)
        for row in rows.values():
            row['warehouses'][0]['stock']=200;row['warehouses'][1]['stock']=0
        return rows
    monkeypatch.setattr(owned,'owned_warehouse_allocations',allocations)
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
                with runtime.store._connect_readonly() as db:
                    db.execute('BEGIN');facts=readback._tiktok_failed_facts(db,active,target)
                source=readback._tiktok_failed_source(active,facts)
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
        from PIL import Image,JpegImagePlugin,features,__version__ as pillow_version
        def sha(value):return hashlib.sha256(value).hexdigest()
        def ref(path):return {'path':str(path.resolve()),'sha256':sha(path.read_bytes())}
        def save_json(path,value):path.write_text(json.dumps(value),encoding='utf-8');return ref(path)
        binding=facts['plan']['payload']['r3_marketplace_binding']
        approved_assets=[row for name in ('generation_result','translation_result')
            for row in binding['documents'][name].get('assets',[])]
        assets=[];ordered=[];cdn_detail=deepcopy(detail)
        for index,url in enumerate(expected['expected_images']):
            original=next(row for row in approved_assets if row['public_url']==url)
            source_path=__import__('pathlib').Path(original['artifact_path'])
            image=Image.open(source_path);image.load()
            output=io.BytesIO();image.save(output,format='JPEG',quality=87,subsampling=2,progressive=False,optimize=False)
            jpeg_path=tmp_path/f'official-{index}.jpg';jpeg_path.write_bytes(output.getvalue())
            jpeg=Image.open(io.BytesIO(output.getvalue()));jpeg.load()
            uri=f'tos-test-object/original-{index}';cdn_url=f'https://cdn.example.invalid/{uri}~gallery.jpeg'
            response_ref=save_json(tmp_path/f'get-{index}.json',{'url':cdn_url,'local_path':str(jpeg_path.resolve()),
                'byte_sha256':sha(output.getvalue()),'mime':'image/jpeg','dimensions':list(jpeg.size)})
            assets.append({'source_url':url,'source_ref':ref(source_path),'cdn_ref':ref(jpeg_path),'cdn_url':cdn_url,
                'cdn_response_ref':response_ref,'parameters':{'qtables':jpeg.quantization,
                    'sampling':JpegImagePlugin.get_sampling(jpeg),'progressive':False}})
            ordered.append({'position':index+1,'asset_index':index,'official_uri':uri})
        cdn_detail['main_images']=[{'uri':row['official_uri'],'urls':[assets[i]['cdn_url']]} for i,row in enumerate(ordered)]
        cdn_detail['description']=miaoshou_rich_description(expected['expected_description'],
            [f"https://cdn.example.invalid/{row['official_uri']}~description.webp" for row in ordered])
        # GET Product omits the zero row; current Inventory Search has explicit
        # main=200 and authoritative total=200 for each exact official SKU.
        for row in cdn_detail['skus']:row['inventory']=[item for item in row['inventory'] if item['quantity']]
        detail_ref=save_json(tmp_path/'original-official-detail.json',{'identity':{'target_label':label,'miaoshou_shop_id':internal,
            'official_shop_id':'911','official_shop_region':'PH','cipher_sha256':sha(b'exact-original-cipher')},
            'exact_product_id':'301','response':{'code':0,'data':cdn_detail}})
        scope_binding=json.loads(facts['scope']['binding_json'])
        media_document={'schema_version':'native-tiktok-deterministic-media-equivalence/v1',
            'binding':{'offer_id':facts['snapshot']['offer_id'],'plan_id':facts['plan']['plan_id'],
                'payload_digest':facts['plan']['payload_digest'],'snapshot_digest':facts['snapshot']['snapshot_digest'],
                'candidate_digest':scope_binding['candidate_digest'],'product_revision':facts['snapshot']['product_revision'],
                'r2_identity_digest':binding['r2_identity']['identity_digest']},
            'codec':{'pillow_version':pillow_version,'jpeg_codec_version':features.version_codec('jpg'),
                'libjpeg_turbo_version':features.version_feature('libjpeg_turbo'),'optimize':False},
            'assets':assets,'targets':{label:{'execution_identity_sha256':readback._ozon_sha(source['identity']),
                'control_shop_id':internal,'region':'PH','official_shop_id':'911','official_product_id':'301',
                'official_detail_ref':detail_ref,'ordered':ordered}}}
        media_ref=save_json(tmp_path/'media-equivalence.json',media_document)
        def official(method,path,access,query=None,body=None,*args,**kwargs):
            wire.append((method,path));assert access=='synthetic-read-only'
            if path==SHOP_PATH:return {'code':1 if state['mode']=='auth-nonzero' else 0,'data':{'shops':[{'id':'911','name':'Original Store','region':'PH','cipher':'exact-original-cipher'}]}}
            if path==SEARCH:
                assert method=='POST' and body=={'status':'ACTIVATE','seller_skus':[x['model_sku'] for x in command['skus']]}
                return {'code':0,'data':{'products':[{'id':'301','skus':detail['skus']}],'total_count':1,'next_page_token':''}}
            if path=='/product/202309/inventory/search':
                assert method=='POST' and query=={'shop_cipher':'exact-original-cipher'} and body=={'sku_ids':[row['id'] for row in detail['skus']]}
                rows=[{'id':row['id'],'seller_sku':row['seller_sku'],'total_available_quantity':200,'total_committed_quantity':4,
                    'warehouse_inventory':[{'warehouse_id':item['warehouse_id'],'available_quantity':item['quantity'],'committed_quantity':4}
                        for item in row['inventory'] if item['quantity']]} for row in detail['skus']]
                if state['mode']=='cdn-stock-missing':rows[0].pop('total_available_quantity')
                if state['mode']=='cdn-stock-remainder':rows[0]['total_available_quantity']=201
                if state['mode']=='cdn-stock-negative':rows[0]['warehouse_inventory'][0]['available_quantity']=-1
                if state['mode']=='cdn-stock-wrong-sku':rows[0]['id']='9999'
                if state['mode']=='cdn-stock-sku-alias-conflict':rows[0]['sku_id']='9999'
                if state['mode']=='cdn-stock-committed-conflict':rows[0]['total_committed_quantity']=0
                product={'product_id':'301','skus':rows}
                if state['mode']=='cdn-stock-product-alias-conflict':product['id']='9999'
                return {'code':0,'data':{'inventory':[product]}}
            assert method=='GET' and path==DETAIL.format(product_id='301') and query=={'shop_cipher':'exact-original-cipher'}
            value=deepcopy(cdn_detail if state['mode'].startswith('cdn-') else detail)
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
            if state['mode']=='cdn-uri-drift':value['main_images'][0]['uri']='tos-test-object/unapproved'
            if state['mode']=='cdn-description-drift':value['description']=value['description'].replace('original-0~description','unapproved~description')
            if state['mode']=='cdn-description-host-drift':value['description']=value['description'].replace('cdn.example.invalid','unapproved.example.invalid')
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
        state['mode']='cdn-match'
        held=readback.observe_submissions(runtime,decision['decision_id'],target_labels=(label,))
        assert next(x for x in runtime.store.get_run(rid)['targets'] if x['target_label']==label)['status']=='FAILED',held
        # One real consumer contract covers genuine raw CDN responses, exact
        # codec equivalence, inventory conservation and protected store CAS.
        for mode in ('cdn-stock-missing','cdn-stock-remainder','cdn-stock-negative','cdn-stock-wrong-sku',
                'cdn-stock-sku-alias-conflict','cdn-stock-product-alias-conflict','cdn-stock-committed-conflict',
                'cdn-uri-drift','cdn-description-drift','cdn-description-host-drift'):
            state['mode']=mode
            held=readback.observe_submissions(runtime,decision['decision_id'],target_labels=(label,),media_equivalence_ref=media_ref)
            assert next(x for x in runtime.store.get_run(rid)['targets'] if x['target_label']==label)['status']=='FAILED',held
        # A caller-supplied JSON object/bare success flag has no proof authority.
        held=readback.observe_submissions(runtime,decision['decision_id'],target_labels=(label,),media_equivalence_ref={'verified':True})
        assert next(x for x in runtime.store.get_run(rid)['targets'] if x['target_label']==label)['status']=='FAILED',held
        # A decodable JPEG with identical pixels plus an extra trailing marker
        # is insufficient: the representation contract requires exact bytes.
        bad_document=deepcopy(media_document);bad_asset=bad_document['assets'][0]
        bad_jpeg=tmp_path/'not-exact.jpg';bad_jpeg.write_bytes((tmp_path/'official-0.jpg').read_bytes()+b'owned-extra-bytes')
        bad_asset['cdn_ref']=ref(bad_jpeg)
        bad_receipt=json.loads(__import__('pathlib').Path(bad_asset['cdn_response_ref']['path']).read_bytes())
        bad_receipt.update(local_path=str(bad_jpeg.resolve()),byte_sha256=ref(bad_jpeg)['sha256'])
        bad_asset['cdn_response_ref']=save_json(tmp_path/'not-exact-get.json',bad_receipt)
        bad_ref=save_json(tmp_path/'not-exact-ref.json',bad_document)
        state['mode']='cdn-match'
        held=readback.observe_submissions(runtime,decision['decision_id'],target_labels=(label,),media_equivalence_ref=bad_ref)
        assert next(x for x in runtime.store.get_run(rid)['targets'] if x['target_label']==label)['status']=='FAILED',held
        assert 'REENCODED_BYTES_NOT_EXACT' in held['observations'][0]['error']
        # After outside-lock encoding succeeded, a fixture file changes at the
        # actual store boundary. The original protected CAS must reject it.
        original_record=runtime.store._record_native_tiktok_failed_readback
        changed_path=tmp_path/'official-0.jpg';unchanged_bytes=changed_path.read_bytes()
        def changed_at_cas(proof):
            changed_path.write_bytes(unchanged_bytes+b'changed-owned-fixture')
            try:return original_record(proof)
            finally:changed_path.write_bytes(unchanged_bytes)
        monkeypatch.setattr(runtime.store,'_record_native_tiktok_failed_readback',changed_at_cas)
        state['mode']='cdn-match'
        held=readback.observe_submissions(runtime,decision['decision_id'],target_labels=(label,),media_equivalence_ref=media_ref)
        assert next(x for x in runtime.store.get_run(rid)['targets'] if x['target_label']==label)['status']=='FAILED',held
        monkeypatch.setattr(runtime.store,'_record_native_tiktok_failed_readback',original_record)
        state['mode']='cdn-match'
        result=readback.observe_submissions(runtime,decision['decision_id'],target_labels=(label,),media_equivalence_ref=media_ref)
        after=next(x for x in result['run']['targets'] if x['target_label']==label)
        assert after['status']=='SUCCEEDED',result
        assert after['attempts']==before['attempts'] and after['failure_events']==before['failure_events']
        assert not after['submission'] and after['readback']['evidence']['verified'] is True
        assert len(after['readback']['evidence']['official_catalog_rows'])==3
        proof=after['readback']['evidence']
        assert proof['official_responses'][3]['response']['data']==cdn_detail
        assert proof['media_equivalence']['kind']=='DETERMINISTIC_REPRESENTATION_EQUIVALENCE'
        assert proof['media_equivalence']['description_rendition_bytes_verified'] is False
        assert proof['inventory_evidence']['missing_row_default_zero'] is False
        assert proof['inventory_evidence']['skus'][0]['derived_zero_warehouse_ids']
        assert proof['inventory_evidence']['skus'][0]['total_committed_quantity']==4
        from shared_platform.native_companion_promotions import canonical_prerequisite
        prerequisite=canonical_prerequisite(after,label)
        assert prerequisite['external_id']==after['external_id']=='301'
        with runtime.store._connect_readonly() as db:
            assert {name:[tuple(row) for row in db.execute('SELECT * FROM '+name)] for name in preserved}==preserved
        assert sum(kind=='owned-original-publish' for kind,path in wire)==1
        assert all(path in {PUBLISH_PATH,MIAOSHOU_SHOPS,SHOP_PATH,SEARCH,DETAIL.format(product_id='301'),'/product/202309/inventory/search'} for kind,path in wire)
        count=len(wire)
        for selected in ((label,label),('tiktok:UNKNOWN_SHOP',)):
            with pytest.raises((ValueError,RuntimeError),match='EXACT_SELECTED_READBACK_TARGETS_REQUIRED'):
                readback.observe_submissions(runtime,decision['decision_id'],target_labels=selected)
        unchanged=readback.observe_submissions(runtime,decision['decision_id'],target_labels=(label,))
        assert len(wire)==count and next(x for x in unchanged['run']['targets'] if x['target_label']==label)==after
    finally:runtime.close()
