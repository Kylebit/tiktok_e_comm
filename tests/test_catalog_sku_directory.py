import json,sqlite3
import pytest
from test_catalog_cost_projection import catalog,catalog_http,full,packet,project,read
from shared_platform.catalog_sku_costs import grouped,state,save
from modules.catalog.sku_directory import list_skus


def seed_global(path):
    with sqlite3.connect(path) as c:
        for n,sku,cost in [('1','660018',5),('2','990018',5.5)]:
            c.execute('INSERT INTO shops(cipher,region) VALUES(?,?)',('shop'+n,'MY' if n=='1' else 'TH'))
            c.execute("INSERT INTO products(sku_id,shop_cipher,product_id,seller_sku,product_name,sku_name,status,global_product_id,global_sku_id) VALUES(?,?,?,?,?,?,'ACTIVATE','global-product','global-variant')",('v'+n,'shop'+n,'p'+n,sku,'Same product','Same specification'))
            c.execute('INSERT INTO sku_costs(sku_id,cost_cny,note) VALUES(?,?,?)',('v'+n,cost,'original manual'))


def test_official_global_one_row_conflict_cas_and_profit(catalog,catalog_http):
    seed_global(catalog);request,_=catalog_http
    data=list_skus();assert data['total']==1 and data['coverage']['source_identities']==2
    row=data['items'][0];assert row['cost']['amount'] is None and row['cost']['choices']==['5','5.5']
    from domains.data_operations.profit_settlement.local_catalog import load_local_catalog
    before=load_local_catalog(catalog)
    assert {'660018','990018'}<=set(before.blocked_skus)
    assert '660018' not in before.costs_by_sku and '990018' not in before.costs_by_sku
    assert {str(v) for v in before.cost_candidates_by_sku['660018']}=={'5','5.5'}
    from modules.products.costs import get_all_costs
    assert 'v1' not in get_all_costs() and 'v2' not in get_all_costs()
    status,_,raw=request('POST','/api/catalog/cost',{'entity_key':row['entity_key'],'cost_cny':'6','revision':row['cost']['revision']})
    assert status==200 and json.loads(raw)['consumer_count']==2
    assert list_skus()['items'][0]['cost']['amount']=='6'
    with pytest.raises(ValueError,match='revision_conflict'):save(catalog,row['entity_key'],'7',row['cost']['revision'])
    with sqlite3.connect(catalog) as c:
        assert c.execute('SELECT cost_cny,note FROM sku_costs ORDER BY sku_id').fetchall()==[(5,'original manual'),(5.5,'original manual')]
        assert c.execute('SELECT count(*) FROM catalog_sku_costs').fetchone()[0]==1
        assert c.execute('SELECT count(*) FROM catalog_sku_cost_history').fetchone()[0]==1
    from domains.data_operations.profit_settlement.local_catalog import load_local_catalog
    historical=load_local_catalog(catalog)
    assert {'660018','990018'}<=set(historical.blocked_skus)
    assert '660018' not in historical.costs_by_sku and '990018' not in historical.costs_by_sku
    actual=load_local_catalog(catalog,cost_view='current')
    assert actual.costs_by_sku['660018']==actual.costs_by_sku['990018']==6
    assert actual.cost_records_by_sku['660018'][0]['source']['table']=='catalog_sku_costs'
    from modules.products.costs import get_all_costs
    assert get_all_costs()['v1']==get_all_costs()['v2']==6
    for n,sku in [('1','660018'),('2','990018')]:
        i=full('shop'+n,'v'+n,'tiktok');i.update(product_id='p'+n,seller_sku=sku)
        assert read(catalog,i)['amount']=='6'


def test_same_internal_text_groups_targets_and_preserves_coverage(catalog):
    seed_global(catalog)
    project(catalog,packet())
    project(catalog,packet(shop='102',receipt='second'))
    data=list_skus();assert data['total']==2 and data['coverage']['source_identities']==4
    assert len([r for r in data['items'] if r['sku']=='0002'])==1
    assert data['coverage']['internal_skus']==2


def test_preserves_scoped_manual_and_publication_consumer(catalog):
    project(catalog,packet())
    row=list_skus()['items'][0];save(catalog,row['entity_key'],'31',row['cost']['revision'])
    before=packet(cost='12',receipt='late');frozen=json.dumps(before,sort_keys=True)
    result=project(catalog,before)
    assert result['rows'][0]['status']=='COST_CONFLICT_PRESERVED'
    assert json.dumps(before,sort_keys=True)==frozen and read(catalog,full())['amount']=='31'
    with sqlite3.connect(catalog) as c:assert c.execute('SELECT amount FROM catalog_identity_costs').fetchone()[0]=='8'
    from shared_platform.catalog_cost_projection import save_manual
    save_manual(catalog,full(),'32',2)
    assert list_skus()['items'][0]['cost']['amount']=='32'


def test_membership_change_rejects_stale_cost(catalog):
    seed_global(catalog);row=list_skus()['items'][0]
    with sqlite3.connect(catalog) as c:c.execute("UPDATE products SET seller_sku='990019' WHERE sku_id='v2'")
    assert list_skus()['total']==2
    with pytest.raises(ValueError,match='revision_conflict'):save(catalog,row['entity_key'],'99',row['cost']['revision'])


@pytest.mark.parametrize('raw,expected',[('0001','0001'),('1','0001'),('660001','0001'),('770001','0001'),('880001','0001'),('990001','0001'),('560001','560001'),('1234567890123456789','1234567890123456789'),('item_0001','item_0001'),('660001_red','660001_red')])
def test_internal_sku_normalization_is_bounded(raw,expected):
    from shared_platform.internal_catalog_sku import internal_sku
    assert internal_sku(raw)==expected


def test_internal_0001_cross_platform_single_cost_and_profit(catalog,catalog_http):
    seed_global(catalog)
    with sqlite3.connect(catalog) as c:
        c.execute("UPDATE products SET seller_sku=CASE sku_id WHEN 'v1' THEN '660001' ELSE '990001' END,global_sku_id=sku_id")
        c.execute('UPDATE sku_costs SET cost_cny=5')
        for shop in [101,102]:
            c.execute('INSERT INTO shopee_shops(shop_id,region) VALUES(?,?)',(shop,'MY' if shop==101 else 'PH'))
            c.execute("INSERT INTO shopee_products(model_id,shop_id,item_id,seller_sku,product_name,model_name) VALUES(?,?,?,'0001','Product','Spec')",(str(shop),shop,str(shop)))
    request,_=catalog_http;status,_,body=request('GET','/api/catalog/skus?q=0001');data=json.loads(body)
    assert status==200 and data['total']==1 and data['items'][0]['sku']=='0001'
    row=data['items'][0];assert row['member_count']==4 and row['cost']['amount']=='5'
    from domains.data_operations.profit_settlement.local_catalog import load_local_catalog
    before=load_local_catalog(catalog);assert before.costs_by_sku['0001']==before.costs_by_sku['660001']==5
    status,_,body=request('POST','/api/catalog/cost',{'entity_key':row['entity_key'],'revision':row['cost']['revision'],'cost_cny':'6'})
    assert status==200 and json.loads(body)['consumer_count']==4
    historical=load_local_catalog(catalog)
    assert historical.costs_by_sku['0001']==historical.costs_by_sku['660001']==historical.costs_by_sku['990001']==5
    after=load_local_catalog(catalog,cost_view='current')
    assert after.costs_by_sku['0001']==after.costs_by_sku['660001']==after.costs_by_sku['990001']==6


def test_preserves_previous_entity_manual_cost_during_internal_merge(catalog):
    seed_global(catalog)
    from shared_platform.catalog_sku_costs import SCHEMA
    from shared_platform.catalog_cost_projection import digest
    old_key='tiktok-global:'+digest(['global-product','global-variant'])
    with sqlite3.connect(catalog) as c:
        c.executescript(SCHEMA);c.execute('INSERT INTO catalog_sku_costs VALUES(?,?,?,?)',(old_key,'17',4,'old user edit'))
    row=list_skus()['items'][0];assert row['cost']['amount']=='17'
    save(catalog,row['entity_key'],'18',row['cost']['revision'])
    with sqlite3.connect(catalog) as c:assert c.execute('SELECT amount,version,source_json FROM catalog_sku_costs WHERE entity_key=?',(old_key,)).fetchone()==('17',4,'old user edit')


def test_unsaved_single_global_cost_inherits_for_missing_member(catalog):
    seed_global(catalog)
    with sqlite3.connect(catalog) as c:c.execute("DELETE FROM sku_costs WHERE sku_id='v2'")
    assert list_skus()['items'][0]['cost']['amount']=='5'
    from domains.data_operations.profit_settlement.local_catalog import load_local_catalog
    actual=load_local_catalog(catalog)
    assert actual.costs_by_sku['660018']==actual.costs_by_sku['990018']==5
    from modules.products.costs import get_all_costs
    assert get_all_costs()['v1']==get_all_costs()['v2']==5


def test_images_reject_unregistered_private_redirect_and_credentials(tmp_path,monkeypatch):
    from shared_platform.catalog_images import ImageCache
    c=sqlite3.connect(':memory:');c.execute('CREATE TABLE products(image_url)');c.execute('CREATE TABLE shopee_products(image_url)')
    cache=ImageCache(c,tmp_path/'images')
    with pytest.raises(ValueError,match='not_registered'):cache.get('https://127.0.0.1/a.png')
    for url in ['http://example.com/a.png','https://user:password@example.com/a.png','https://example.com/a.png?access_token=secret']:
        with pytest.raises(ValueError):cache._read(url)
    import socket,http.client
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))])
    with pytest.raises(ValueError,match='non_public'):cache._read('https://example.com/a.png')
    monkeypatch.setattr(socket,'getaddrinfo',lambda *a,**k:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443))])
    class Redirect:
        def __init__(self,*a,**k):pass
        def request(self,*a,**k):pass
        def getresponse(self):return type('Response',(),{'status':302})()
        def close(self):pass
    monkeypatch.setattr(http.client,'HTTPSConnection',Redirect)
    with pytest.raises(ValueError,match='http_302'):cache._read('https://example.com/a.png')
