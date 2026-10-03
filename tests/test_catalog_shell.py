import json
from test_catalog_cost_projection import catalog,catalog_http,packet
from shared_platform.catalog_cost_projection import project


def test_review_copy_real_handler_data_mode_and_cost_save(catalog,catalog_http):
    request,httpd=catalog_http
    httpd.catalog_review_copy={'mode':'LOCAL_REVIEW_COPY','captured_at':'2026-09-07T09:28:13Z','cost_changes':'COPY_ONLY'}
    project(catalog,packet())
    status,_,raw=request('GET','/api/catalog/products?limit=1')
    data=json.loads(raw)
    assert status==200 and data['ok'] and data['total']==1 and len(data['items'])==1
    assert data['review_copy']['cost_changes']=='COPY_ONLY'
    identity=data['items'][0]['cost_controls'][0]['identity']
    status,_,raw=request('POST','/api/catalog/cost',{'identity':identity,'expected_version':1,'cost_cny':'17'})
    assert status==200 and json.loads(raw)['version']==2
    status,_,raw=request('GET','/api/catalog/products?limit=1')
    assert json.loads(raw)['items'][0]['cost_controls'][0]['cost_cny']=='17'


def test_missing_catalog_database_remains_visible_failure(catalog,catalog_http,tmp_path,monkeypatch):
    from core import config
    request,_=catalog_http
    monkeypatch.setattr(config,'_cache',{'database':str(tmp_path/'missing.db')})
    status,_,raw=request('GET','/api/catalog/products')
    data=json.loads(raw)
    assert status==500 and data['ok'] is False and 'database not found' in data['error']
    assert 'items' not in data


def test_five_direct_primary_routes_and_legacy_redirects(catalog,catalog_http):
    request,_=catalog_http
    status,_,raw=request('GET','/api/orbit/navigation')
    assert status==200
    rows=json.loads(raw)['navigation']
    assert [(r['label'],r['href']) for r in rows]==[('商品目录','/catalog'),('商品上架','/product-workspace'),('供应链','/supply-chain/'),('利润','/profit'),('知识工具','/knowledge')]
    assert all(r['level']=='primary' for r in rows)
    for home in ('/', '/index.html', '/?view=tasks'):
        status,_,raw=request('GET',home)
        assert status==200 and b'/static/task_workspace.js' in raw
    for old,new in [('/?view=knowledge','/knowledge'),('/?view=product','/product-workspace'),('/?view=supply-chain','/supply-chain/')]:
        status,headers,_=request('GET',old)
        assert status==308 and headers['Location']==new
    for page in ('/catalog','/product-workspace','/supply-chain/','/knowledge'):
        status,_,raw=request('GET',page)
        assert status==200 and b'/static/operations_shell.js' in raw and b'/static/operations_shell.css' in raw
        assert b'data-operations-nav' not in raw and b'id="domainCards"' not in raw
    status,_,raw=request('GET','/profit')
    assert status==200 and b'/static/profit_hub.js' in raw
    assert b'/profit-original/artifacts/profit_reports_monthly/2026-07/index.html' in raw
