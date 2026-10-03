import hashlib,json,sqlite3
import struct,zlib
import pytest
from pathlib import Path
from email.message import Message
from types import SimpleNamespace
from test_i05_profit_facts import captured_profile
from domains.data_operations.profit_settlement.local_catalog import load_local_catalog
from domains.data_operations.profit_settlement.http_review import handle_captured_review

def save(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def sku_fixture(folder):
    p=captured_profile(folder)
    with sqlite3.connect(p['catalog_path']) as db:db.execute("UPDATE sku_costs SET cost_cny='8'")
    c=load_local_catalog(p['catalog_path']);identity=c.review['records'][0]['identity']
    p.update(ad_rate='0.22',sku_price_basis='listing')
    samples=[{'identity':identity,'order_id':'SYNTHETIC-'+str(i),'status':'settled','quantity':1,'paid_product_amount':str(paid),'net_settlement_amount':str(paid/2),'currency':'THB','settled_at':'2026-08-05T12:00:00+07:00','net_basis':'platform_net_before_external_cost_and_ads'} for i,paid in enumerate([100,200,300])]
    source={'schema_version':'profit-sku-capture/v1','scope':{k:p[k] for k in ('platform','site','shop_id','start','end','timezone')},'catalog_sha256':sha(p['catalog_path']),'catalog_snapshot_id':c.snapshot_id,'fx_sha256':sha(p['fx_path']),
      'entries':[{'identity':identity,'listing':{'amount':'250','currency':'THB','source':'SYNTHETIC listing export','version':'listing-1','as_of':'2026-08-06T00:00:00+07:00'},'samples':{'source':'SYNTHETIC settled rows','version':'sample-1','as_of':'2026-08-10T00:00:00+07:00','time_basis':'settled_at','rows':samples}}]}
    path=folder/'sku-evidence.json';save(path,source);p.update(sku_evidence_path=str(path),sku_evidence_sha256=sha(path))
    return p,source,identity

def resave(p,source):save(Path(p['sku_evidence_path']),source);p['sku_evidence_sha256']=sha(p['sku_evidence_path'])

def repin_catalog(p,source):
    source.update(catalog_sha256=sha(p['catalog_path']),catalog_snapshot_id=load_local_catalog(p['catalog_path']).snapshot_id)
    resave(p,source)

def scenario(folder,name):
    folder.mkdir(parents=True,exist_ok=True)
    p,s,i=sku_fixture(folder)
    with sqlite3.connect(p['catalog_path']) as db:
        db.execute("INSERT INTO shops VALUES ('other','other','TH')")
        db.execute("INSERT INTO products VALUES ('other-variant','OTHER-9000','Synthetic','One','','THB','other',20,'other-product')")
        db.execute("INSERT INTO sku_costs VALUES ('other-variant','99',20)")
        db.execute("INSERT INTO shopee_shops VALUES ('shopee-other','TH')")
        db.execute("INSERT INTO shopee_products VALUES ('SHOPEE-9000','Synthetic','One','','THB','TH','shopee-other',20,'item-other','model-other',999)")
        if name=='conflict':db.execute("UPDATE sku_costs SET cost_cny='9' WHERE sku_id='variant' AND updated_at=10")
        if name=='no-cost':db.execute("DELETE FROM sku_costs WHERE sku_id='variant'")
    if name=='missing':
        s['entries'][0].pop('listing');s['entries'][0]['samples']['rows']=[]
    if name=='ready':
        assets=folder/'sku-assets';assets.mkdir()
        def chunk(kind,data):return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data))
        raw=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',80,80,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+bytes([225,170,65])*80)*80))+chunk(b'IEND',b'')
        (assets/'synthetic.png').write_bytes(raw);s['entries'][0]['image']={'file':'synthetic.png','sha256':sha(assets/'synthetic.png')}
    repin_catalog(p,s)
    return p,s,i

def request(profile,identity=None):
    body={'profile':profile,'mode':'sku','sku_identity':identity};headers=Message()
    for k,v in {'Host':'127.0.0.1:12345','Origin':'http://127.0.0.1:12345','Content-Type':'application/json','Content-Length':str(len(json.dumps(body).encode()))}.items():headers[k]=v
    result={}
    handler=SimpleNamespace(headers=headers,server=SimpleNamespace(server_address=('127.0.0.1',12345)),_read_json=lambda:body,_json=lambda status,payload:result.update(status=status,payload=payload))
    handle_captured_review(handler,method='POST',body_limit=65536)
    return result['status'],result['payload']

def test_actual_bridge_selects_full_identity_and_distinct_prices(tmp_path):
    p,s,identity=sku_fixture(tmp_path);status,value=request(p,identity)
    save(tmp_path/'response.json',value)
    assert status==200 and value['schema_version']=='profit-captured-sku/v1'
    assert value['listing']['amount']=='250' and value['recent']['median']==200
    assert value['estimate']['profit_cny']==6.0
    assert value['approval_status']=='ESTIMATE_ONLY' and value['cost']['selected']['unit_cost_cny']=='8'
    assert value['estimate']['comp_ship_stats'] is None

@pytest.mark.parametrize('name',['ready','conflict','no-cost','missing'])
def test_missing_and_conflicting_sources_are_explicit(tmp_path,name):
    p,s,i=scenario(tmp_path,name);status,v=request(p,i);save(tmp_path/'response.json',v)
    assert status==200
    assert v['image']['state']==('available' if name=='ready' else 'unknown')
    if name=='ready':assert v['estimate']['profit_cny']==6.0
    else:
        assert v['estimate'] is None
        assert ('cost_unresolved' if name in {'conflict','no-cost'} else 'samples_missing_or_empty') in v['gaps']
    if name=='missing':assert v['listing'] is None and v['recent']['median'] is None
    if name=='conflict':assert len(v['cost']['candidates'])==2 and v['cost']['issues']

@pytest.mark.parametrize('field,value',[('shop_key','other'),('platform','shopee'),('seller_sku','9000'),('product_id','other-product'),('variant_id','other-variant')])
def test_cross_scope_and_suffix_do_not_select(tmp_path,field,value):
    p,s,i=scenario(tmp_path,'ready');status,v=request(p)
    assert status==200 and [x['identity'] for x in v['choices']]==[i]
    status,v=request(p,{**i,field:value});assert status==400 and v['estimate'] is None

@pytest.mark.parametrize('kind',['sku','catalog','fx'])
def test_source_mutation_invalidates_estimate(tmp_path,kind):
    p,s,i=sku_fixture(tmp_path);assert request(p,i)[1]['estimate']
    if kind=='sku':s['entries'][0]['listing']['amount']='251';save(Path(p['sku_evidence_path']),s)
    elif kind=='catalog':
        with sqlite3.connect(p['catalog_path']) as db:db.execute("UPDATE sku_costs SET cost_cny='7'")
    else:save(Path(p['fx_path']),{'rates_cny':{'THB':'0.3'},'source':'CHANGED','as_of':'2026-08-06T00:00:00Z'})
    status,v=request(p,i);save(tmp_path/'after.json',v);assert v['estimate'] is None
    assert status==400 if kind!='fx' else 'fx_missing_or_changed' in v['gaps']

@pytest.mark.parametrize('kind',['identity','currency','time','net_basis','duplicate','clipped','unsettled'])
def test_invalid_or_ineligible_samples_never_estimate(tmp_path,kind):
    p,s,i=sku_fixture(tmp_path);rows=s['entries'][0]['samples']['rows']
    if kind=='identity':rows[0]['identity']={**i,'shop_key':'other'}
    if kind=='currency':rows[0]['currency']='MYR'
    if kind=='time':rows[0]['settled_at']='2026-08-10T00:00:00+07:00'
    if kind=='net_basis':rows[0]['net_basis']='unknown'
    if kind=='duplicate':rows.append(rows[0])
    if kind=='clipped':rows[0]['order_id']='SYNTH...'
    if kind=='unsettled':
        for row in rows:row['status']='unsettled'
    resave(p,s);v=request(p,i)[1];assert v['estimate'] is None
    assert 'samples_missing_or_empty' in v['gaps']

@pytest.mark.parametrize('ref',[{'file':'../outside.png','sha256':'0'*64},{'file':'https://outside.invalid/image.png','sha256':'0'*64},{'file':'synthetic.png','sha256':'0'*64}])
def test_untrusted_image_references_are_unknown(tmp_path,ref):
    p,s,i=scenario(tmp_path,'ready');s['entries'][0]['image']=ref;resave(p,s)
    v=request(p,i)[1];assert v['image']['state']=='unknown' and v['estimate']['profit_cny']==6.0

def test_explicit_basis_and_fx_preserve_existing_pure_model(tmp_path):
    p,s,i=sku_fixture(tmp_path);p['sku_price_basis']='recent_median';v=request(p,i)[1]
    assert v['estimate']['profit_cny']==3.2 and v['listing']['amount']=='250'
    p['fx_overrides']={'THB':'0.3'};v=request(p,i)[1]
    assert v['estimate']['profit_cny']==8.8 and v['fx']['base']['rates_cny']['THB']=='0.2'
    assert v['model']['sha256']==sha(Path(__file__).resolve().parents[1]/'modules/finance/sku_profit_model.py')
    p.pop('ad_rate');assert request(p,i)[1]['estimate'] is None

def test_exact_sku_collision_remains_unresolved(tmp_path):
    p,s,i=scenario(tmp_path,'ready')
    with sqlite3.connect(p['catalog_path']) as db:db.execute("UPDATE products SET seller_sku='TEST-9000' WHERE shop_cipher='other'")
    repin_catalog(p,s);v=request(p,i)[1]
    assert v['cost']['selected'] is None and v['estimate'] is None
