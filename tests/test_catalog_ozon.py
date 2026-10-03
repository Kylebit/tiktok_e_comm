"""Synthetic, guarded Ozon catalog consumers; never loads personal credentials."""
from copy import deepcopy
import json
import sqlite3
import pytest
from test_catalog_cost_projection import catalog, catalog_http
from shared_platform.catalog_cost_projection import project
from shared_platform.catalog_ozon import identity, read_cost, save_manual
from shared_platform.catalog_ozon_readback import ExactOzonQueries, INFO, ATTRIBUTES, DESCRIPTION


def full(account='101'):
    return dict(platform='ozon', identity_kind='product_offer', shop_key=account,
                product_id='301', offer_id='660002', seller_sku='660002')


def packet(account='101', receipt='first', cost='8'):
    return {'schema':'catalog-ozon-observation/v1', 'receipt_id':receipt,
            'snapshot_digest':'a'*64, 'rows':[{'authority':'OFFICIAL', 'verified':True,
            'identity':full(account), 'variant_key':'one', 'approved_cost':cost,
            'listing':{'name':'Ozon synthetic product', 'specification':{'size':'one'},
                       'price':'20', 'approved_currency':'CNY', 'images':[], 'statuses':{'status':'processed'}}}]}


def read(path, account='101'):
    with sqlite3.connect(path) as conn:
        conn.row_factory=sqlite3.Row
        return read_cost(conn,full(account))


def approved_snapshot():
    from test_approved_publication_snapshot import _approved_plan,_rebind
    from domains.product_operations import build_approved_publication_snapshot
    plan=_approved_plan()
    for row in plan['payload']['pricing']['selected_targets']['ozon:RU']['sku_prices']:
        row.update(currency='CNY',list_price='40',old_price_cny='52')
    _rebind(plan)
    return build_approved_publication_snapshot(plan).payload()


def raw_responses(variants):
    from decimal import Decimal
    result={}
    for n,v in enumerate(variants):
        offer=v['offer_id'];pid=7002+n
        identity_fields={'id':pid,'offer_id':offer}
        info={**identity_fields,'name':v['title'],'price':v['price'],'old_price':v['old_price'],
              'images':['provider://image-'+str(i) for i in range(v['image_count'])],
              'description_category_id':int(v['category']['id']),
              'statuses':{'is_created':True,'status':'PUBLISHED','status_failed':''}}
        attr={**identity_fields,'attributes':[], 'weight':str(Decimal(v['parcel']['weight_kg'])*1000),
              'weight_unit':'g','dimension_unit':'mm',
              **{key:str(Decimal(value)*10) for key,value in zip(('depth','width','height'),v['parcel']['package_cm'])}}
        result[offer]={
            INFO:{'body':{'offer_id':[offer]},'response':{'items':[info]}},
            ATTRIBUTES:{'body':{'filter':{'offer_id':[offer],'visibility':'ALL'},'limit':1000},'response':{'result':[attr]}},
            DESCRIPTION:{'body':{'offer_id':offer},'response':{'result':{**identity_fields,'description':v['description']}}}}
    return result


def test_actual_http_edit_audit_profit_and_legacy_reference(catalog,catalog_http,monkeypatch):
    from modules.catalog import listings
    legacy={'0002':{'product_id':'unknown-id-type','seller_sku':'660002','migrated':True,'product_name':'Historical'}}
    frozen=deepcopy(legacy)
    monkeypatch.setattr(listings,'load_ozon_by_key',lambda:legacy)
    assert project(catalog,packet())['status']=='COMPLETE'
    request,_=catalog_http
    status,_,body=request('GET','/api/catalog/products?sku=0002')
    assert status==200,body
    data=json.loads(body)
    typed=[r for r in data['items'] if r.get('identity')]
    refs=[r for r in data['items'] if r.get('is_reference_only')]
    assert len(typed)==len(refs)==1
    assert data['business_total']==data['reference_total']==1
    assert data['summary']['ozon_keys']==data['summary']['total_keys']==1
    assert refs[0]['ozon']['identity_status']=='LEGACY_UNBOUND_REFERENCE'
    assert not refs[0]['matched']['ozon'] and refs[0]['cost_controls']==[]
    control=typed[0]['cost_controls'][0]
    assert control['identity']==full() and 'variant_id' not in control['identity']
    status,_,body=request('POST','/api/catalog/cost',{'identity':full(),'expected_version':1,'cost_cny':'23'})
    assert status==200,body
    assert read(catalog)['amount']=='23'
    from domains.data_operations.profit_settlement.local_catalog import load_local_catalog
    actual=load_local_catalog(catalog)
    assert str(actual.costs_by_sku['660002'])=='23',actual.review
    assert actual.cost_records_by_sku['660002'][0]['source']['table']=='catalog_sku_costs'
    assert legacy==frozen


def test_same_ids_across_accounts_and_late_receipts(catalog):
    from domains.data_operations.profit_settlement.local_catalog import load_local_catalog
    project(catalog,packet())
    inherited=load_local_catalog(catalog,cost_view='current')
    assert inherited.cost_records_by_sku['660002'][0]['source']['table']=='catalog_sku_resolution'
    assert inherited.cost_records_by_sku['660002'][0]['source']['source_kind']=='INHERITED'
    project(catalog,packet('102','second','13'))
    # Simulate two retained pre-migration account costs. New projection already
    # inherits the internal SKU value and deliberately does not overwrite it.
    with sqlite3.connect(catalog) as conn:
        conn.execute('INSERT INTO catalog_ozon_costs VALUES(?,?,?,?,?,?,?)',('102','301','660002','13',1,'MANUAL','old-account-cost'))
    unresolved=load_local_catalog(catalog,cost_view='current')
    assert '660002' in unresolved.blocked_skus and '660002' not in unresolved.costs_by_sku
    for row in unresolved.review['records']:
        assert {c['amount'] for c in row['cost']['candidates']}=={'8','13'}
        assert all(not c['usable_identity_match'] and c['source']['source_ref'].startswith('canonical') for c in row['cost']['candidates'])
    issue_codes={issue['code'] for issue in unresolved.review['issues']}
    assert 'canonical_sku_cost_conflict' in issue_codes
    assert 'scoped_cost_identity_conflict' not in issue_codes
    save_manual(catalog,full(),'19',1)
    result=project(catalog,packet(receipt='late',cost='9'))
    assert result['status']=='NEEDS_REVIEW'
    assert read(catalog)['amount']=='19' and read(catalog,'102')['amount']=='19'
    assert project(catalog,packet())['replayed'] is True
    with pytest.raises(ValueError,match='version_conflict'):save_manual(catalog,full(),'99',1)
    actual=load_local_catalog(catalog,cost_view='current')
    assert '660002' not in actual.blocked_skus and actual.costs_by_sku['660002']==19
    costs={r['identity']['shop_key']:r['cost']['candidates'][0]['amount'] for r in actual.review['records']}
    assert costs=={'101':'19','102':'19'}


def test_actual_browser_cost_identity_and_legacy_provenance(catalog,catalog_http,tmp_path,monkeypatch):
    import os,subprocess
    from pathlib import Path
    from modules.catalog import listings
    monkeypatch.setattr(listings,'load_ozon_by_key',lambda:{'0002':{'seller_sku':'660002','product_name':'Historical','migrated':True}})
    project(catalog,packet());project(catalog,packet('102','other','13'))
    _,httpd=catalog_http
    child=subprocess.run([os.environ['ORBIT_NODE_BIN'],str(Path(__file__).with_name('browser')/'catalog_ozon.cjs'),f'http://127.0.0.1:{httpd.server_port}',str(tmp_path)],capture_output=True,text=True,encoding='utf-8',timeout=90)
    assert child.returncode==0,child.stdout+child.stderr
    assert read(catalog)['amount']=='31' and read(catalog,'102')['amount']=='31'


@pytest.mark.parametrize('change',['product','offer'])
def test_preexisting_account_offer_mapping_conflict_is_not_replaced(catalog,change):
    project(catalog,packet());p=packet(receipt='new')
    if change=='product':p['rows'][0]['identity']['product_id']='302'
    else:p['rows'][0]['identity'].update(offer_id='660003',seller_sku='660003')
    with pytest.raises(ValueError,match='existing_identity_conflict'):project(catalog,p)
    assert read(catalog)['amount']=='8'


@pytest.mark.parametrize('stage',['after_observation','after_cost','before_receipt'])
def test_atomic_projection_and_retry(catalog,stage):
    def fault(point):
        if point==stage:raise OSError('synthetic storage failure')
    with pytest.raises(OSError):project(catalog,packet(),fault=fault)
    with sqlite3.connect(catalog) as conn:
        for table in ('catalog_ozon_products','catalog_ozon_costs','catalog_projection_receipts'):
            assert conn.execute('SELECT count(*) FROM '+table).fetchone()[0]==0
    assert project(catalog,packet())['status']=='COMPLETE'


@pytest.mark.parametrize('change',['variant','duplicate','offer_conflict','snapshot'])
def test_invalid_packet_never_partially_projects(catalog,change):
    p=packet()
    if change=='variant':p['rows'][0]['identity']['variant_id']='301'
    elif change=='duplicate':p['rows'].append(deepcopy(p['rows'][0]))
    elif change=='offer_conflict':p['rows'][0]['identity']['seller_sku']='660003'
    else:p['snapshot_digest']='bad'
    with pytest.raises(ValueError):project(catalog,p)
    with sqlite3.connect(catalog) as conn:
        assert conn.execute("SELECT count(*) FROM sqlite_master WHERE name='catalog_ozon_products'").fetchone()[0]==0 or conn.execute('SELECT count(*) FROM catalog_ozon_products').fetchone()[0]==0


def response(path):
    row={'id':301,'offer_id':'660002'}
    if path==DESCRIPTION:return {'result':row}
    return {'items' if path==INFO else 'result':[row]}


def test_query_allowlist_exact_scope_and_linkage():
    calls=[]
    def post(path,body):calls.append((path,body));return response(path)
    q=ExactOzonQueries(post,('660002',))
    q(INFO,{'offer_id':['660002'],'visibility':'ALL'})
    q(ATTRIBUTES,{'filter':{'offer_id':['660002']}})
    q(DESCRIPTION,{'offer_id':'660002'})
    assert [p for p,_ in calls]==[INFO,ATTRIBUTES,DESCRIPTION]
    assert calls[0][1]=={'offer_id':['660002']}
    for path in ('/v3/product/import','/v2/products/stocks','/v1/warehouse/list'):
        with pytest.raises(ValueError,match='path_denied'):q(path,{})
    with pytest.raises(ValueError,match='scope_conflict'):q(INFO,{'offer_id':['other']})
    assert len(calls)==3


@pytest.mark.parametrize('bad',['duplicate','extra','total','missing','has_next','last_id','id'])
def test_query_incomplete_or_ambiguous_set_fails_closed(bad):
    value=response(INFO)
    if bad=='duplicate':value['items']*=2
    elif bad=='extra':value['items'][0]['offer_id']='other'
    elif bad=='total':value['total']=2
    elif bad=='missing':value['items']=[]
    elif bad=='has_next':value['has_next']=True
    elif bad=='last_id':value['last_id']='301'
    else:value['items'][0].pop('id')
    q=ExactOzonQueries(lambda *_:value,('660002',))
    with pytest.raises(ValueError):q(INFO,{'offer_id':['660002']})


@pytest.mark.parametrize('path',[ATTRIBUTES,DESCRIPTION])
def test_each_query_must_bind_same_official_product(path):
    def post(current,body):
        value=response(current)
        if current==path:
            row=value['result'][0] if current==ATTRIBUTES else value['result']
            row['id']=302
        return value
    q=ExactOzonQueries(post,('660002',));q(INFO,{'offer_id':['660002']})
    with pytest.raises(ValueError,match='identity_conflict'):
        q(path,{'filter':{'offer_id':['660002']}} if path==ATTRIBUTES else {'offer_id':'660002'})


def test_account_drift_stops_before_transport(monkeypatch):
    from modules.ozon import config as ozconfig, client
    from shared_platform.catalog_ozon_readback import bound_query_transport
    monkeypatch.setattr(ozconfig,'ozon_credentials',lambda:('102','synthetic'))
    monkeypatch.setattr(client,'ozon_post_bound',lambda *a,**kw:pytest.fail('must not contact provider'))
    with pytest.raises(ValueError,match='account_unavailable'):
        bound_query_transport({'account_id':'101','credential_ref':'ozon-config-account'})


def test_existing_producer_response_shape_without_total_uses_actual_parser():
    # Shape retained by test_product_publication_live_dependencies.py:1008.
    # Do not add fictional metadata and call this a real producer success.
    from shared_platform.product_publication_live_dependencies import OfficialOzonV4Transport
    calls=[]
    def post(path,body):
        calls.append((path,body))
        if path==ATTRIBUTES:return {'result':[{'id':7654321,'offer_id':'0967','type_id':999,'attributes':[],
                                             'weight':200,'weight_unit':'g','depth':100,'width':80,'height':20,'dimension_unit':'mm'}]}
        if path==DESCRIPTION:return {'result':{'id':7654321,'offer_id':'0967','description':'Approved Ozon description'}}
        return {'items':[{'id':7654321,'offer_id':'0967','name':'Approved Ozon title',
                          'price':'100.00','old_price':'120.00','images':['provider://image'],
                          'description_category_id':17028913,
                          'statuses':{'is_created':True,'status':'CREATED','status_failed':''}}]}
    from shared_platform.catalog_ozon_readback import read_exact_offers
    rows=read_exact_offers(('0967',),post)
    assert rows[0]['id']==7654321 and rows[0]['weight_kg']=='0.2'
    assert [p for p,_ in calls]==[INFO,ATTRIBUTES,DESCRIPTION]
    assert calls[0][1]=={'offer_id':['0967']}


@pytest.mark.parametrize('provider_status',[
    {'is_created':True,'status':'PUBLISHED','status_failed':''},
    {'is_created':True,'status':'NEW_PROVIDER_STATE','status_failed':''},
    {'is_created':True,'status_failed':''},
    {'is_created':True,'status':{'unexpected':'structure'},'status_failed':''},
])
def test_actual_producer_to_catalog_without_fictional_total(catalog,tmp_path,provider_status):
    from modules.ozon.approved_publication_v4 import build_ozon_v4_executor, project_ozon_v4_variants
    from shared_platform.product_publication_live_dependencies import OfficialOzonV4Transport
    from shared_platform.product_publication_runner import PublicationPlatformRequest
    from shared_platform.catalog_publication_sync import CatalogPublicationSync
    snapshot=approved_snapshot();variants=project_ozon_v4_variants(snapshot,target_labels=('ozon:RU',))
    responses=raw_responses(variants);calls=[]
    for response in responses.values():
        response[INFO]['response']['items'][0]['statuses']=deepcopy(provider_status)
    def post(path,body):
        assert path in {INFO,ATTRIBUTES,DESCRIPTION},'unexpected provider write'
        calls.append((path,deepcopy(body)))
        offers=body['filter']['offer_id'] if path==ATTRIBUTES else body['offer_id']
        if path==DESCRIPTION:return deepcopy(responses[offers][path]['response'])
        key='items' if path==INFO else 'result'
        return {key:[deepcopy(responses[o][path]['response'][key][0]) for o in offers]}
    transport=OfficialOzonV4Transport(post=post,account_id='101')
    sink=CatalogPublicationSync(catalog,tmp_path/'outbox')
    request=PublicationPlatformRequest('producer','report','OZON',('ozon:RU',),snapshot,catalog_sink=sink)
    executor=build_ozon_v4_executor(dispatch_variant=transport.dispatch_variant,
        readback_variants=transport.readback_variants,catalog_account_resolver=transport.catalog_account,
        catalog_observer=transport.catalog_observations)
    result=executor(request)
    assert result['targets'][0]['status']=='PUBLISHED' and result['external_write_count']==0,result
    assert any(s['state']=='COMPLETE' for s in sink.status()),sink.status()
    assert not sink.failures
    from domains.data_operations.profit_settlement.local_catalog import load_local_catalog
    actual=load_local_catalog(catalog)
    assert set(actual.costs_by_sku)==set(responses)
    with sqlite3.connect(catalog) as conn:
        documents=[json.loads(row[0]) for row in conn.execute('SELECT document_json FROM catalog_ozon_products')]
    assert all(document['statuses']==provider_status for document in documents)
    expected_state=provider_status.get('status') if isinstance(provider_status.get('status'),str) else None
    for row in actual.review['records']:
        assert row['listing_status']==expected_state
        evidence=row['listing_status_evidence']
        assert evidence['raw']==provider_status and evidence['normalization']=='NOT_NORMALIZED'
        assert evidence['reason']==('provider_status_is_not_a_publication_or_stock_outcome' if expected_state else 'provider_status_missing_or_invalid')
        assert row['sale_currency'] is None and row['approved_sale_currency']=='CNY'
        assert row['sale_currency_evidence']=={'observed':None,'approved':'CNY','approved_source_ref':snapshot['snapshot_digest'],'reason':'official_currency_not_captured'}
        display=actual.product_by_seller_sku[row['identity']['seller_sku']]
        assert display['listing_status']==expected_state and display['listing_status_evidence']==evidence
        assert display['currency'] is None and display['approved_currency']=='CNY'
        assert display['currency_evidence']==row['sale_currency_evidence']
    strict=calls[-3*len(variants):]
    assert len(strict)==3*len(variants)
    for n,v in enumerate(variants):
        assert strict[3*n][1]=={'offer_id':[v['offer_id']]}


@pytest.mark.parametrize('mode',['normal','local_failure','outbox_failure','manual','scope_failure'])
def test_actual_executor_frozen_outbox_and_recovery(catalog,tmp_path,monkeypatch,mode):
    from test_product_publication_runner import _snapshot
    from test_ozon_approved_publication_v4 import _published_item
    from modules.ozon.approved_publication_v4 import build_ozon_v4_executor, OzonDispatchFact, project_ozon_v4_variants
    from shared_platform.product_publication_runner import PublicationPlatformRequest
    from shared_platform.catalog_publication_sync import CatalogPublicationSync
    from shared_platform import catalog_ozon_readback as queries
    snapshot=approved_snapshot();frozen=deepcopy(snapshot)
    variants=project_ozon_v4_variants(snapshot,target_labels=('ozon:RU',))
    sink=CatalogPublicationSync(tmp_path/'missing.db' if mode=='local_failure' else catalog,tmp_path/'outbox')
    request=PublicationPlatformRequest('run-ozon','report-ozon','OZON',('ozon:RU',),snapshot,catalog_sink=sink)
    intent=sink.begin(request);original=sink._write
    if mode in {'outbox_failure','scope_failure'}:
        def write(path,value):
            if mode=='outbox_failure' and value['packet']['schema']=='catalog-ozon-observation/v1':raise OSError('synthetic outbox failure')
            if mode=='scope_failure' and value.get('ozon_recovery'):raise OSError('synthetic intent failure')
            return original(path,value)
        monkeypatch.setattr(sink,'_write',write)
    existing={};dispatches=[]
    def dispatch(variant):
        dispatches.append(variant['offer_id'])
        existing[variant['offer_id']]=_published_item(variant,item_id=7001+len(dispatches))
        if mode=='manual' and len(dispatches)==1:
            p=packet();p['rows'][0]['identity'].update(product_id='7002',offer_id=variant['offer_id'],seller_sku=variant['offer_id'])
            project(catalog,p);save_manual(catalog,p['rows'][0]['identity'],'37',1)
        return OzonDispatchFact('ACCEPTED')
    executor=build_ozon_v4_executor(dispatch_variant=dispatch,
        readback_variants=lambda offers:[existing[o] for o in offers if o in existing],
        catalog_observer=lambda actual:queries.observations({'account_id':'101','credential_ref':'ozon-config-account'},actual,list(existing.values())),
        catalog_account_resolver=lambda:{'account_id':'101','credential_ref':'ozon-config-account'})
    result=executor(request)
    assert snapshot==frozen
    if mode=='scope_failure':
        assert result['targets'][0]['status']=='FAILED' and not dispatches
        return
    assert result['targets'][0]['status']=='PUBLISHED',result
    assert len(dispatches)==len(variants)
    restored=CatalogPublicationSync(catalog,tmp_path/'outbox')
    states=restored.status()
    if mode=='outbox_failure':
        assert sink.failures and len(states)==1
        import os,sys,subprocess,shutil
        from pathlib import Path
        for recovery_mode in ('normal','manual','account_drift','missing','duplicate','wrong_attribute','pagination','timeout'):
            owned=tmp_path/recovery_mode;owned.mkdir()
            shutil.copytree(tmp_path/'outbox',owned/'outbox')
            source_conn=sqlite3.connect(catalog);dest_conn=sqlite3.connect(owned/'shop.db')
            try:source_conn.backup(dest_conn)
            finally:source_conn.close();dest_conn.close()
            responses=raw_responses(variants);first=variants[0]['offer_id']
            if recovery_mode=='missing':responses[first][INFO]['response']['items']=[]
            elif recovery_mode=='duplicate':responses[first][INFO]['response']['items']*=2
            elif recovery_mode=='wrong_attribute':responses[first][ATTRIBUTES]['response']['result'][0]['id']=999
            elif recovery_mode=='pagination':responses[first][ATTRIBUTES]['response']['has_next']=True
            scenario={'platform':'OZON','mode':recovery_mode,'receipt_id':intent,'responses':responses,
                      'identity':{**full(),'product_id':'7002','offer_id':first,'seller_sku':first}}
            (owned/'scenario.json').write_text(json.dumps(scenario),encoding='utf-8')
            child=subprocess.run([sys.executable,'-I','-S','-B',str(Path(__file__).with_name('catalog_recovery_process.py')),str(owned)],capture_output=True,text=True,encoding='utf-8',timeout=40,
                                 env={**os.environ,'B4B_BOUND_TEMP':str(tmp_path)})
            assert child.returncode==0,child.stdout+child.stderr
            evidence=json.loads((owned/'RECOVERY.json').read_text())
            assert evidence['pid']!=os.getpid() and not evidence['denied']
            assert all(c['method']=='POST' and c['path'] in queries.QUERY_PATHS for c in evidence['calls'])
        assert len(dispatches)==len(variants)
        return
    elif mode=='local_failure':
        receipt=next(r['receipt_id'] for r in states if r['state']=='PENDING_LOCAL_PROJECTION')
        assert restored.recover(receipt)['state']=='COMPLETE'
    elif mode=='manual':
        assert any(r['state']=='NEEDS_REVIEW' for r in states),states
    else:assert any(r['state']=='COMPLETE' for r in states),states
    assert len(dispatches)==len(variants)
    with sqlite3.connect(catalog) as conn:
        conn.row_factory=sqlite3.Row
        from shared_platform.catalog_ozon import catalog_rows
        rows=catalog_rows(conn)
        assert len(rows)==len(variants)
        assert all('variant_id' not in r['identity'] for r in rows)
        if mode=='manual':assert rows[0]['cost']['amount']=='37'
