from copy import deepcopy
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest

from core import config, db
from shared_platform.catalog_cost_projection import project, save_manual, read_cost


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    path=tmp_path/'shop.db'
    monkeypatch.setattr(config,'_cache',{'database':str(path)})
    db.init_db()
    # Finish the fixture's WAL checkpoint before tests snapshot the DB bytes.
    conn = sqlite3.connect(path)
    try:
        with conn:
            conn.execute('CREATE TABLE product_analytics(product_id TEXT,shop_cipher TEXT,synced_at INTEGER)')
    finally:
        conn.close()
    return path


def full(shop='101', variant='501', platform='shopee'):
    return dict(platform=platform,shop_key=shop,product_id='301',variant_id=variant,seller_sku='0002')


def packet(shop='101', variant='501', cost='8', receipt='first'):
    return {'schema':'catalog-publication-observation/v1','receipt_id':receipt,'snapshot_digest':'a'*64,
            'rows':[{'authority':'OFFICIAL','verified':True,'identity':full(shop,variant),
                     'variant_key':'source-size-one','approved_cost':cost,
                     'listing':{'name':'Test product','variant_name':'One','region':'MY','currency':'MYR','price':'20','status':'NORMAL'}}]}


def read(path, i):
    with sqlite3.connect(path) as conn:
        conn.row_factory=sqlite3.Row
        return read_cost(conn,i)


def test_projection_real_catalog_and_profit_consumer(catalog,monkeypatch):
    p=packet();result=project(catalog,p)
    assert result['status']=='COMPLETE'
    assert read(catalog,full())['amount']=='8'
    with sqlite3.connect(catalog) as conn:
        assert conn.execute('SELECT model_id,item_id,seller_sku FROM shopee_products').fetchone()==('501','301','0002')
    from domains.data_operations.profit_settlement.local_catalog import load_local_catalog
    # Publication readback creates a current directory cost, not a dated
    # historical cost observation for a profit-settlement month.
    actual=load_local_catalog(catalog,cost_view='current')
    assert str(actual.costs_by_sku['0002'])=='8'
    assert actual.cost_records_by_sku['0002'][0]['source']['table']=='catalog_sku_resolution'
    from modules.catalog.listings import _identity_cost_controls
    controls=_identity_cost_controls([{'platform':'shopee','shop_id':101,'sku_id':'501','product_id':'301','seller_sku':'0002','region':'MY'}])
    assert controls[0]['cost_cny']=='8' and controls[0]['identity']==full()


def test_same_internal_sku_shares_cost_across_shops(catalog):
    project(catalog,packet(cost='8'))
    project(catalog,packet(shop='102',cost='13',receipt='second'))
    save_manual(catalog,full('101'),'19',1)
    assert read(catalog,full('101'))['amount']=='19'
    assert read(catalog,full('102'))['amount']=='19'
    from domains.data_operations.profit_settlement.local_catalog import load_local_catalog
    actual=load_local_catalog(catalog,cost_view='current')
    assert '0002' not in actual.blocked_skus
    assert actual.costs_by_sku['0002']==19
    costs={row['identity']['shop_key']:row['cost']['candidates'][0]['amount'] for row in actual.review['records']}
    assert costs=={'101':'19','102':'19'}


def test_same_platform_variant_id_different_internal_skus_remain_separate(catalog):
    project(catalog,packet(cost='8'))
    other=packet(shop='102',cost='13',receipt='other-sku');other['rows'][0]['identity']['seller_sku']='0003'
    project(catalog,other);save_manual(catalog,full('101'),'19',1)
    assert read(catalog,{**full('102'),'seller_sku':'0003'})['amount']=='13'


def test_first_late_readback_preserves_manual_cost(catalog):
    project(catalog,packet())
    save_manual(catalog,full(),'17',1)
    # A different/new approved publication receipt must also preserve the edit.
    result=project(catalog,packet(cost='9',receipt='late'))
    assert result['rows'][0]['status']=='COST_CONFLICT_PRESERVED'
    assert read(catalog,full())['amount']=='17'
    assert project(catalog,packet())['replayed'] is True
    assert read(catalog,full())['amount']=='17'


def test_first_projection_protects_preexisting_legacy_manual(catalog):
    with sqlite3.connect(catalog) as conn:
        conn.execute("INSERT INTO shopee_products(model_id,shop_id,item_id,seller_sku) VALUES('501',101,'301','0002')")
        conn.execute("INSERT INTO sku_costs(sku_id,cost_cny,note) VALUES('501',23,'manual before readback')")
    result=project(catalog,packet())
    assert result['rows'][0]['status']=='COST_CONFLICT_PRESERVED'
    assert float(read(catalog,full())['amount'])==23
    from domains.data_operations.profit_settlement.local_catalog import load_local_catalog
    assert load_local_catalog(catalog,cost_view='current').costs_by_sku['0002']==23


@pytest.mark.parametrize('bad',['','item_301'])
def test_unknown_official_variant_is_not_invented(catalog,bad):
    result=project(catalog,packet(variant=bad))
    assert result['status']=='NEEDS_REVIEW' and result['rows'][0]['status']=='UNBOUND'
    with sqlite3.connect(catalog) as conn:
        assert conn.execute('SELECT count(*) FROM shopee_products').fetchone()[0]==0


@pytest.mark.parametrize('stage',['after_observation','after_cost','before_receipt'])
def test_transaction_failure_has_no_partial_rows_and_local_retry(catalog,stage):
    def fail(current):
        if current==stage:raise OSError('injected local storage failure')
    with pytest.raises(OSError):project(catalog,packet(),fault=fail)
    with sqlite3.connect(catalog) as conn:
        for table in ['shopee_products','catalog_identity_costs','catalog_official_observations','catalog_projection_receipts']:
            assert conn.execute('SELECT count(*) FROM '+table).fetchone()[0]==0
    assert project(catalog,packet())['status']=='COMPLETE'


def test_concurrent_replay_and_manual_cas(catalog):
    with ThreadPoolExecutor(max_workers=2) as pool:
        results=list(pool.map(lambda _:project(catalog,packet()),range(2)))
    assert sum(not r['replayed'] for r in results)==1
    save_manual(catalog,full(),'12',1)
    with pytest.raises(ValueError,match='version_conflict'):save_manual(catalog,full(),'15',1)
    assert read(catalog,full())['amount']=='12'


def test_outbox_recovery_uses_frozen_snapshot_and_no_dispatch(catalog,tmp_path,monkeypatch):
    from test_product_publication_runner import _snapshot
    from shared_platform.product_publication_runner import PublicationPlatformRequest
    from shared_platform.catalog_publication_sync import CatalogPublicationSync
    snapshot=_snapshot();before=json.dumps(snapshot,sort_keys=True)
    sku=snapshot['skus'][0];target=next(t['target_label'] for t in snapshot['publication_targets'] if t['platform']=='shopee')
    request=PublicationPlatformRequest('run-one','report-one','SHOPEE',(target,),snapshot)
    observations=[]
    for n,sku in enumerate(snapshot['skus']):
        observation=deepcopy(packet(variant=str(501+n))['rows'][0])
        observation.update(model_sku=sku['model_sku'],target_label=target)
        observation['identity']['seller_sku']=sku['model_sku']
        observations.append(observation)
    sync=CatalogPublicationSync(tmp_path/'not-created.db',tmp_path/'outbox')
    result=sync.capture(request,observations)
    assert result['state']=='PENDING_LOCAL_PROJECTION'
    restored=CatalogPublicationSync(catalog,tmp_path/'outbox')
    assert restored.recover(result['receipt_id'])['state']=='COMPLETE'
    assert json.dumps(snapshot,sort_keys=True)==before
    assert restored.recover(result['receipt_id'])['result']['replayed'] is True


@pytest.fixture
def catalog_http(catalog,tmp_path,monkeypatch):
    import threading
    import http.client
    from http.server import ThreadingHTTPServer
    from modules.products import server
    from modules.catalog import ozon_data
    from shared_platform.catalog_publication_sync import CatalogPublicationSync
    monkeypatch.setattr(config,'_cache',{'database':str(catalog),'shopee':{'global_sku_map':str(tmp_path/'no-global-map.json')}})
    monkeypatch.setattr(ozon_data,'_ozon_dir',lambda:tmp_path/'no-ozon-data')
    monkeypatch.setattr(server,'_catalog_publication_sync',lambda:CatalogPublicationSync(catalog,tmp_path/'outbox'))
    server_instance=ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
    thread=threading.Thread(target=server_instance.serve_forever,daemon=True);thread.start()
    def request(method,path,body=None):
        connection=http.client.HTTPConnection('127.0.0.1',server_instance.server_port)
        payload=json.dumps(body) if body is not None else None
        connection.request(method,path,payload,{'Content-Type':'application/json'})
        response=connection.getresponse();raw=response.read();result=(response.status,dict(response.getheaders()),raw);connection.close();return result
    yield request,server_instance
    server_instance.shutdown();server_instance.server_close();thread.join(5)


def test_actual_catalog_http_cost_edit_and_retired_page(catalog,catalog_http):
    request,_=catalog_http
    p=packet();p['rows'][0]['identity']['seller_sku']='660002'
    project(catalog,p)
    status,_,body=request('GET','/api/catalog/products?sku=0002')
    assert status==200,body
    data=json.loads(body)
    controls=data['items'][0]['cost_controls']
    assert controls[0]['cost_cny']=='8'
    status,_,summary=request('GET','/api/catalog/stores')
    assert status==200 and json.loads(summary)['summary']['with_cost']==1,summary
    status,_,body=request('POST','/api/catalog/cost',{'identity':controls[0]['identity'],'expected_version':controls[0]['version'],'cost_cny':'21'})
    assert status==200 and json.loads(body)['version']==2
    status,_,body=request('GET','/api/catalog/products?sku=0002')
    assert json.loads(body)['items'][0]['cost_controls'][0]['cost_cny']=='21'
    status,_,body=request('POST','/api/catalog/cost',{'identity':controls[0]['identity'],'expected_version':1,'cost_cny':'99'})
    assert status==409
    status,_,body=request('POST','/api/catalog/cost',{'match_key':'0002','cost_cny':'99'})
    assert status==409
    for route in ['/costs','/costs.html']:
        status,headers,body=request('GET',route)
        assert status==308 and headers['Location']=='/catalog'


@pytest.mark.parametrize('mode',['normal','manual_during_publish','missing_variant','catalog_failure','outbox_failure','prepare_failure','dispatch_persistence_failure'])
def test_actual_shopee_producer_to_durable_catalog(catalog,tmp_path,monkeypatch,mode):
    from test_product_publication_runner import _snapshot
    from test_shopee_skill_regions import FakeRuntime
    from shared_platform.product_publication_runner import PublicationPlatformRequest
    from shared_platform.product_publication_executors import build_shopee_region_executor
    from shared_platform.catalog_publication_sync import CatalogPublicationSync
    snapshot=_snapshot();skus=snapshot['skus'];target='shopee:PH'
    frozen_before=json.dumps(snapshot,sort_keys=True)
    first_identity=dict(platform='shopee',shop_key='101',product_id='8101',variant_id='7001',seller_sku=skus[0]['model_sku'])
    if mode=='manual_during_publish':
        with sqlite3.connect(catalog) as conn:
            conn.execute('INSERT INTO shopee_products(model_id,shop_id,item_id,seller_sku) VALUES(?,?,?,?)',('7001',101,'8101',skus[0]['model_sku']))
    class OfficialFixture(FakeRuntime):
        def __init__(self):
            super().__init__()
            self.regional_image_ids['PH']=['official-image-'+str(n) for n,_ in enumerate(snapshot['product']['images'])]
            self.localized_copy['PH']={key:snapshot['product'][key] for key in ('title','description')}
        def compatible_logistics(self,*args,**kwargs):return [2001,2002]
        def global_models(self,*args):return [{'global_model_sku':s['model_sku'],'tier_index':[n]} for n,s in enumerate(skus)]
        def regional_models(self,*args):
            return [{'model_id':'' if mode=='missing_variant' and n==1 else str(7001+n),'model_sku':s['model_sku'],'tier_index':[n],
                      'price_info':[{'currency':s['prices'][target]['currency'],'original_price':s['prices'][target]['amount']}]} for n,s in enumerate(skus)]
        def publish_task_result(self,*args):
            if mode=='manual_during_publish':
                save_manual(catalog,first_identity,'37',0)
            return super().publish_task_result(*args)
        def regional_item(self,context,item_id):
            row=super().regional_item(context,item_id)
            row.update(item_sku=skus[0]['seller_sku'],category_id=101944)
            row['image']['image_url_list']=snapshot['product']['images']
            return row
    runtime=OfficialFixture();sync=CatalogPublicationSync(tmp_path/'missing.db' if mode=='catalog_failure' else catalog,tmp_path/'outbox')
    request=PublicationPlatformRequest('shopee-official-test','report-one','SHOPEE',(target,),snapshot,catalog_sink=sync)
    sync.begin(request)
    if mode in {'outbox_failure','prepare_failure','dispatch_persistence_failure'}:
        original_write=sync._write
        def fail_observation(path,value):
            if mode=='prepare_failure' and 'recovery' in value:raise OSError('injected scope persistence failure')
            if mode=='dispatch_persistence_failure' and 'dispatch' in value:raise OSError('injected task persistence failure')
            if mode!='prepare_failure' and value['packet']['schema']=='catalog-publication-observation/v1':raise OSError('injected disk failure after official readback')
            return original_write(path,value)
        monkeypatch.setattr(sync,'_write',fail_observation)
    execute=build_shopee_region_executor(global_item_id_resolver=lambda request:'60000001',runtime=runtime)
    from shared_platform import product_publication_executors as executors
    original_readback=executors.readback_dispatched_regions
    observed_readbacks=[]
    def observe_readback(*args,**kwargs):
        result=original_readback(*args,**kwargs)
        observed_readbacks.append(result)
        return result
    monkeypatch.setattr(executors,'readback_dispatched_regions',observe_readback)
    result=execute(request)
    assert json.dumps(snapshot,sort_keys=True)==frozen_before
    if mode=='prepare_failure':
        assert result['targets'][0]['status']=='FAILED' and len(runtime.create_calls)==0
        return
    if mode=='missing_variant':
        assert result['targets'][0]['status']!='PUBLISHED'
        assert any(r['state']=='NEEDS_REVIEW' for r in sync.status())
        with sqlite3.connect(catalog) as conn:assert conn.execute('SELECT count(*) FROM shopee_products').fetchone()[0]==0
        return
    assert result['targets'][0]['status']=='PUBLISHED',(result,observed_readbacks)
    states=sync.status()
    if mode=='dispatch_persistence_failure':
        assert states[0]['state']=='PENDING_OFFICIAL_EVIDENCE'
        assert len(runtime.create_calls)==1
        restored=CatalogPublicationSync(catalog,tmp_path/'outbox')
        assert restored.recover_official(states[0]['receipt_id'])['code']=='exact_persisted_query_identity_missing'
        assert len(runtime.create_calls)==1
        return
    if mode=='outbox_failure':
        assert states[0]['state']=='PENDING_OFFICIAL_EVIDENCE'
        assert sync.failures[0]['code']=='CATALOG_EVIDENCE_PERSISTENCE_FAILED'
        assert sync.recover(states[0]['receipt_id'])['requires_official_readback'] is True
        import os,sys,subprocess,shutil,time
        from pathlib import Path
        from modules.shopee.skill_regions import TASK_RESULT_PATH
        context=runtime.context('PH')
        gets={
            TASK_RESULT_PATH:{'owner':500,'params':{'publish_task_id':9101},'response':{'response':{'publish_status':'success','item_id':'8101'}}},
            '/api/v2/product/get_item_base_info':{'owner':101,'params':{'item_id_list':'8101'},'response':{'response':{'item_list':[runtime.regional_item(context,'8101')]}}},
            '/api/v2/product/get_model_list':{'owner':101,'params':{'item_id':8101},'response':{'response':{'model':runtime.regional_models(context,'8101')}}},
            '/api/v2/global_product/get_global_item_id':{'owner':500,'params':{'shop_id':101,'item_id_list':'8101'},'response':{'response':{'item_id_map':[{'item_id':'8101','global_item_id':'60000001'}]}}},
            '/api/v2/global_product/get_global_model_list':{'owner':500,'params':{'global_item_id':60000001},'response':{'response':{'global_model':runtime.global_models(context,'60000001')}}},
        }
        for recovery_mode in ['normal','get_error','wrong_identity','wrong_linkage','missing_variant','duplicate_variant','manual_conflict','expired_token','wrong_shop','localized_missing','localized_wrong_digest','localized_replaced','old_intent']:
            owned=tmp_path/recovery_mode;owned.mkdir()
            shutil.copytree(tmp_path/'outbox',owned/'outbox');shutil.copyfile(catalog,owned/'shop.db')
            scenario={'mode':recovery_mode,'receipt_id':states[0]['receipt_id'],'identity':first_identity,'gets':deepcopy(gets),
                      'tokens':{'shops':{'101':{'shop_id':101,'region':'PH','access_token':'synthetic-shop','expire_at':int(time.time())+3600}},'merchants':{'500':{'merchant_id':500,'access_token':'synthetic-merchant','expire_at':int(time.time())+3600}}}}
            if recovery_mode=='wrong_identity':scenario['gets']['/api/v2/product/get_item_base_info']['response']['response']['item_list'][0]['item_id']='wrong'
            if recovery_mode=='expired_token':scenario['tokens']['shops']['101']['expire_at']=0
            if recovery_mode=='wrong_shop':scenario['tokens']['shops']['101']['shop_id']=999
            if recovery_mode=='wrong_linkage':scenario['gets']['/api/v2/global_product/get_global_item_id']['response']['response']['item_id_map'][0]['global_item_id']='999'
            if recovery_mode=='missing_variant':scenario['gets']['/api/v2/product/get_model_list']['response']['response']['model'][1]['model_id']=''
            if recovery_mode=='duplicate_variant':scenario['gets']['/api/v2/product/get_model_list']['response']['response']['model'].append(deepcopy(scenario['gets']['/api/v2/product/get_model_list']['response']['response']['model'][0]))
            if recovery_mode.startswith('localized_'):
                from test_approved_publication_snapshot import _approved_plan,_rebind
                from domains.product_operations import build_approved_publication_snapshot
                plan=_approved_plan();base=plan['payload']['product_facts']['image_urls']
                plan['payload']['localized_image_routing']={'schema_version':'localized-publication-images/v1','approval_digest':'sha256:'+'1'*64,'supplement_digest':'sha256:'+'2'*64,'source_snapshot_digest':'sha256:'+'3'*64,
                    'routes':{label:{'locale':'en-master','ordered_images':['https://img.example/approved-local-1.jpg','https://img.example/approved-local-2.jpg'] if label==target else list(base)} for label in plan['payload']['targets']}}
                _rebind(plan);localized=build_approved_publication_snapshot(plan).payload()
                local_request=PublicationPlatformRequest('localized-negative-'+recovery_mode,'report-local','SHOPEE',(target,),localized)
                local_sync=CatalogPublicationSync(owned/'shop.db',owned/'outbox')
                scenario['receipt_id']=local_sync.begin(local_request)
                local_sync.prepare_shopee(local_request,'60000001',runtime)
                original_intent=json.loads((tmp_path/'outbox'/(states[0]['receipt_id']+'.json')).read_text(encoding='utf-8'))
                local_sync.record_shopee_dispatch(local_request,{'targets':original_intent['dispatch']})
                if recovery_mode!='localized_missing':
                    mapping={'60000001':{'shop_items':{'PH':{'shop_id':101,'item_id':'9999' if recovery_mode=='localized_replaced' else '8101','localized_images':{'image_route_digest':'sha256:'+'9'*64,'image_ids':['later-version-1','later-version-2']}}}}}
                    (owned/'map.json').write_text(json.dumps(mapping),encoding='utf-8')
            if recovery_mode=='old_intent':
                from shared_platform.catalog_cost_projection import digest
                intent_path=owned/'outbox'/(scenario['receipt_id']+'.json')
                legacy=json.loads(intent_path.read_text(encoding='utf-8'))
                for key in ('recovery','recovery_digest','dispatch','dispatch_digest'):legacy.pop(key,None)
                legacy['packet'].pop('snapshot',None);legacy['packet_digest']=digest(legacy['packet'])
                intent_path.write_text(json.dumps(legacy),encoding='utf-8')
            (owned/'scenario.json').write_text(json.dumps(scenario),encoding='utf-8')
            child=subprocess.run([sys.executable,'-I','-S','-B',str(Path(__file__).with_name('catalog_recovery_process.py')),str(owned)],capture_output=True,text=True,encoding='utf-8',timeout=40,
                                 env={**os.environ,'B4B_BOUND_TEMP':str(tmp_path)})
            assert child.returncode==0,child.stdout+child.stderr
            evidence=json.loads((owned/'RECOVERY.json').read_text())
            assert evidence['pid']!=os.getpid() and not evidence['denied']
            assert all(call['method']=='GET' for call in evidence['calls'])
        assert len(runtime.create_calls)==1
        return
    if mode=='catalog_failure':
        receipt=next(r['receipt_id'] for r in states if r['state']=='PENDING_LOCAL_PROJECTION')
        restored=CatalogPublicationSync(catalog,tmp_path/'outbox')
        before=(len(runtime.create_calls),len(runtime.list_calls))
        assert restored.recover(receipt)['state']=='COMPLETE'
        assert before==(len(runtime.create_calls),len(runtime.list_calls))
        sync=restored;states=sync.status()
    if mode=='manual_during_publish':
        assert any(r['state']=='NEEDS_REVIEW' for r in states)
        assert read(catalog,first_identity)['amount']=='37'
        from domains.data_operations.profit_settlement.local_catalog import load_local_catalog
        assert str(load_local_catalog(catalog,cost_view='current').costs_by_sku[skus[0]['model_sku']])=='37'
        return
    assert any(r['state']=='COMPLETE' for r in states),states
    with sqlite3.connect(catalog) as conn:
        rows=conn.execute('SELECT model_id,shop_id,item_id,seller_sku FROM shopee_products ORDER BY model_id').fetchall()
        assert rows==[(str(7001+n),101,'8101',s['model_sku']) for n,s in enumerate(skus)]
    before=(len(runtime.create_calls),len(runtime.list_calls))
    receipt=next(r['receipt_id'] for r in states if r['state']=='COMPLETE')
    assert sync.recover(receipt)['result']['replayed'] is True
    assert before==(len(runtime.create_calls),len(runtime.list_calls))


def test_legacy_cost_import_updates_identity_consumer_and_rejects_ambiguity(catalog,tmp_path):
    from modules.products.costs import import_from_cursor,save_cost
    project(catalog,packet())
    csv=tmp_path/'costs.csv';csv.write_text('SKU ID,cost\n501,29\n',encoding='utf-8')
    assert import_from_cursor(csv)==1
    assert read(catalog,full())['amount']=='29.0'
    from domains.data_operations.profit_settlement.local_catalog import load_local_catalog
    assert str(load_local_catalog(catalog,cost_view='current').costs_by_sku['0002'])=='29.0'
    project(catalog,packet(shop='102',receipt='other-shop'))
    with pytest.raises(ValueError,match='ambiguous'):save_cost('501',99)
    assert read(catalog,full())['amount']=='29.0'
    with pytest.raises(ValueError,match='identity_drift'):
        save_manual(catalog,{**full(),'seller_sku':'wrong-sku'},99,2)


def test_catalog_browser_real_http(catalog,catalog_http,tmp_path):
    import os,subprocess
    from pathlib import Path
    for shop,cost in [('101','8'),('102','13')]:
        p=packet(shop=shop,cost=cost,receipt=shop);p['rows'][0]['identity']['seller_sku']='660002'
        project(catalog,p)
    _,httpd=catalog_http
    source=Path(__file__).resolve().parents[1]
    result=subprocess.run([os.environ['ORBIT_NODE_BIN'],str(source/'tests/browser/catalog_cost_projection.cjs'),f'http://127.0.0.1:{httpd.server_port}',str(tmp_path)],capture_output=True,text=True,encoding='utf-8',timeout=90)
    assert result.returncode==0,result.stdout+result.stderr
    assert read(catalog,{**full(),'seller_sku':'660002'})['amount']=='31'
    assert read(catalog,{**full('102'),'seller_sku':'660002'})['amount']=='31'


def test_partial_observation_preserves_existing_catalog_fields(catalog):
    with sqlite3.connect(catalog) as conn:
        conn.execute("INSERT INTO shopee_products(model_id,shop_id,item_id,seller_sku,image_url,stock,model_name) VALUES('501',101,'301','0002','https://example.invalid/kept.png',7,'Kept name')")
    p=packet();p['rows'][0]['listing']['variant_name']=None
    project(catalog,p)
    with sqlite3.connect(catalog) as conn:
        assert conn.execute('SELECT image_url,stock,model_name FROM shopee_products').fetchone()==('https://example.invalid/kept.png',7,'Kept name')
