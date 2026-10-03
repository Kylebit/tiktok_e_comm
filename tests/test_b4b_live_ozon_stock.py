"""Default server composition with only Ozon HTTP and local scheduling replaced."""
from copy import deepcopy
import hashlib
import json
import pytest
from test_b4b_ozon_partial_budget import real_ozon_store,ozon_payload
from test_b4b_final_candidate_runner import _prepared
from test_product_publication_runner import _report_store


def live_payload():
    payload=ozon_payload()
    payload['product_facts']['category']['name']='Wall Stickers'
    category=payload['product_facts']['categories_by_target']['ozon:RU']['category']
    category.update(id='17027906',name='Interior Stickers',path=[{'id':'14500','name':'Home'},{'id':'17027906','name':'Interior Stickers'}])
    return payload


class OfflineOzonHTTP:
    def __init__(self,mode):
        self.mode=mode;self.calls=[];self.items={};self.stock=0;self.warehouse_reads=0
    def __call__(self,path,body):
        self.calls.append((path,deepcopy(body)))
        if path=='/v1/description-category/tree':
            return {'result':[{'description_category_id':14500,'category_name':'Home','children':[
                {'description_category_id':17027906,'category_name':'Interior Stickers','children':[
                    {'type_id':91971,'type_name':'Interior Sticker','children':[]}]}]}]}
        if path=='/v1/description-category/attribute':
            return {'result':[{'id':i,'is_required':True} for i in (85,9048,8229)]}
        if path=='/v1/description-category/attribute/values/search':
            return {'result':[{'id':126745801,'value':'No brand'}] if body['attribute_id']==85 else [{'id':91971,'value':'Interior Sticker'}]}
        if path=='/v3/product/import':
            assert len(body['items'])==1
            row=deepcopy(body['items'][0]);self.items[row['offer_id']]=row
            return {'result':{'task_id':len(self.items)}}
        if path=='/v3/product/info/list':
            return {'items':[{**deepcopy(row),'id':100+index,'statuses':{'is_created':True,'status':'PUBLISHED','status_failed':''}}
                for index,row in enumerate(self.items.values()) if row['offer_id'] in body['offer_id']]}
        if path=='/v4/product/info/attributes':
            return {'result':[deepcopy(row) for row in self.items.values() if row['offer_id'] in body['filter']['offer_id']]}
        if path=='/v1/product/info/description':
            rows=list(self.items.values());index=next(i for i,row in enumerate(rows) if row['offer_id']==body['offer_id'])
            row=rows[index]
            return {'result':{'offer_id':row['offer_id'],'id':100+index,
                'description':next(a['values'][0]['value'] for a in row['attributes'] if a['id']==4191)}}
        if path=='/v2/warehouse/list':
            self.warehouse_reads+=1
            if self.mode=='local_prepare_failure' and self.warehouse_reads==2:
                return {'warehouses':[]}
            changed=(self.mode=='warehouse_changed_before_submit' and self.warehouse_reads==2
                or self.mode=='warehouse_changed_after_submit' and self.warehouse_reads>=3)
            return {'warehouses':[{'warehouse_id':888 if changed else 765,'status':'active','is_kgt':False}]}
        if path=='/v2/product/info/stocks-by-warehouse/fbs':
            assert body=={'offer_id':list(self.items),'limit':1000}
            return {'result':[{'offer_id':sku,'warehouse_id':765,'present':self.stock,'reserved':0} for sku in body['offer_id']]}
        if path=='/v2/products/stocks':
            assert body['stocks']==[{'offer_id':sku,'warehouse_id':765,'stock':200} for sku in self.items]
            if self.mode=='stock_timeout':
                raise TimeoutError('synthetic POST timeout')
            self.stock=200
            return {'result':[{'offer_id':sku,'errors':[]} for sku in self.items]}
        raise AssertionError(path)


@pytest.mark.parametrize('mode',['normal','local_prepare_failure','stock_timeout','warehouse_changed_before_submit','warehouse_changed_after_submit'])
def test_default_server_ozon_stock_uses_frozen_quantity_and_real_http_budget(tmp_path,monkeypatch,mode):
    from modules.ozon import client
    from modules.products import server
    from shared_platform import product_publication_live_dependencies as live
    from shared_platform.product_publication_runs import ProductPublicationRunStore
    store,plan,value=real_ozon_store(tmp_path,payload=live_payload())
    reports=_report_store(tmp_path)
    value,candidate,approval=_prepared(reports.reports_root.parent/'product-preparation',value)
    inventory=next(row['inventory'] for row in candidate['review_manifest']['targets'] if row['target_label']=='ozon:RU')
    assert inventory['stock_per_model_sku']==200
    assert inventory['warehouse_selection_policy']=='UNIQUE_ACTIVE_OR_CREATED_NON_KGT'
    http=OfflineOzonHTTP(mode)
    credential_path=tmp_path/'ozon-publication-credentials.json'
    credential_path.write_text(json.dumps({'client_id':'4953064','api_key':'synthetic-secret'}),encoding='utf-8')
    monkeypatch.setenv('ORBIT_OZON_CREDENTIALS_PATH',str(credential_path))
    monkeypatch.setenv('ORBIT_OZON_CREDENTIALS_SHA256',hashlib.sha256(credential_path.read_bytes()).hexdigest())
    monkeypatch.setenv('ORBIT_OZON_EXPECTED_ACCOUNT_ID','4953064')
    # These are the actual production classes; only the credential-bound HTTP
    # callable is synthetic. No builder/executor/projection result is faked.
    monkeypatch.setattr(client,'ozon_post_bound',lambda path,body,**_credentials:http(path,body))
    monkeypatch.setattr(server,'_release_store',lambda:store)
    monkeypatch.setattr(server,'_product_publication_report_store',lambda:reports)
    monkeypatch.setattr(server,'_product_publication_run_store',lambda:ProductPublicationRunStore(tmp_path/'runs.db'))
    monkeypatch.setattr(server,'_product_publication_execution_identity',lambda _:{'skill_digest':'1'*64,'git_commit':'2'*40,'code_digest':'3'*64})
    monkeypatch.setattr(server,'_launch_product_publication_background',lambda action:action())
    monkeypatch.setattr(server,'_PRODUCT_PUBLICATION_PLATFORM_EXECUTORS',{})
    server._initialize_product_publication_platform_executors()
    status,started=server._start_product_publication({'offer_id':value['offer_id'],'plan_id':value['plan_id']},platform='OZON')
    assert status==202,started
    report=reports.get_report_by_run(run_id=started['run_id'])
    assert len([c for c in http.calls if c[0]=='/v3/product/import'])==2,json.dumps({'targets':report['targets'],'paths':[c[0] for c in http.calls]},ensure_ascii=False)
    stock_posts=len([c for c in http.calls if c[0]=='/v2/products/stocks'])
    local_refusal=mode in {'local_prepare_failure','warehouse_changed_before_submit'}
    assert stock_posts==(0 if local_refusal else 1),report
    assert report['mutation_budgets'][0]['attempts']['total']==2+stock_posts
    assert report['summary']['evidence']['external_write_count']==(None if mode=='stock_timeout' else 2+stock_posts)
    assert report['status']==('PUBLISHED' if mode=='normal' else 'FAILED' if local_refusal else 'PROCESSING'),report
    if mode=='normal':
        assert json.loads(report['targets'][0]['evidence']['provider_reason'])=={'warehouse_id':765,'stock':'FROZEN_EACH_SKU'}
    before=deepcopy(http.calls)
    assert server._start_product_publication({'offer_id':value['offer_id'],'plan_id':value['plan_id']},platform='OZON')[1]['run_id']==started['run_id']
    assert http.calls==before


def test_prepared_stock_payload_retains_read_warehouse_and_original_values():
    from shared_platform.product_publication_live_dependencies import OfficialOzonV4Transport
    http=OfflineOzonHTTP('normal')
    http.items={'0958':{},'0959':{}}
    updates=({'offer_id':'0958','stock':200},{'offer_id':'0959','stock':200})
    prepared=OfficialOzonV4Transport(post=http).prepare_stock_update(updates)
    updates[0]['stock']=999
    http.mode='warehouse_changed_before_submit'
    assert prepared.warehouse_id==765
    assert prepared.submit().outcome=='ACCEPTED'
    assert http.warehouse_reads==1
    assert http.calls[-1][1]['stocks'][0]=={'offer_id':'0958','stock':200,'warehouse_id':765}


def test_stock_readback_supplies_provider_required_limit_after_official_400_regression():
    from shared_platform.product_publication_live_dependencies import OfficialOzonV4Transport

    calls=[]
    def post(path,body):
        calls.append((path,deepcopy(body)))
        if path=='/v2/warehouse/list':
            return {'warehouses':[{'warehouse_id':765,'status':'active','is_kgt':False}]}
        if path=='/v2/product/info/stocks-by-warehouse/fbs':
            if body.get('limit')!=1000:
                raise RuntimeError('Ozon HTTP 400: required parameter limit is missing')
            return {'result':[{'offer_id':'0958','warehouse_id':765,'present':0,'reserved':0}]}
        raise AssertionError(path)

    assert OfficialOzonV4Transport(post=post).readback_stocks(('0958',))==[
        {'offer_id':'0958','stock':0,'warehouse_id':765}
    ]
    assert calls[-1]==(
        '/v2/product/info/stocks-by-warehouse/fbs',
        {'offer_id':['0958'],'limit':1000},
    )


@pytest.mark.parametrize('kind',['missing','text_true','text_false','integer_zero','none','true'])
def test_warehouse_requires_positive_evidence_of_non_kgt(kind):
    from shared_platform.product_publication_live_dependencies import OfficialOzonV4Transport
    row={'warehouse_id':765,'status':'active'}
    if kind!='missing':
        row['is_kgt']={'text_true':'true','text_false':'false','integer_zero':0,'none':None,'true':True}[kind]
    calls=[]
    def post(path,body):
        calls.append(path)
        assert path=='/v2/warehouse/list'
        return {'warehouses':[row]}
    fact=OfficialOzonV4Transport(post=post).update_stocks(({'offer_id':'0958','stock':200},))
    assert fact.outcome=='PRE_SUBMIT_FAILED'
    assert calls==['/v2/warehouse/list']
