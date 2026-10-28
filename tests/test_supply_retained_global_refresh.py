import copy, csv, hashlib, importlib.util, io, json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import pytest
from test_u04_refresh import project, cli, ROOT
from test_u04_captured_refresh import captured_inputs, resave

SCRIPTS=ROOT/'domains/supply_chain_operations/skills/manage-seaya-replenishment/scripts'
def module(name):
    spec=importlib.util.spec_from_file_location('global_'+name,SCRIPTS/(name+'.py'))
    value=importlib.util.module_from_spec(spec);spec.loader.exec_module(value);return value

def witness(kind, rows):
    columns=list(rows[0]);stream=io.StringIO();writer=csv.DictWriter(stream,fieldnames=columns);writer.writeheader();writer.writerows(rows)
    raw=stream.getvalue();times=['2026-10-05T05:23:16+00:00','2026-10-05T05:24:51+00:00'] if kind=='inventory' else ['2026-10-05T05:29:17+00:00']
    return {'schema':'seaya-global-browser-evidence/v1','kind':kind,'tenantId':'SYNTHETIC TENANT','sourceId':'synthetic-global-'+kind,
        'reference':'synthetic-independent-full-query-witness','scope':{'warehouses':list(module('seaya_capture').WAREHOUSES.values()),'selection':'all_inventory' if kind=='inventory' else 'all_inbound_statuses'},
        'atomicSnapshot':False,'rowToPageBinding':'NOT_RETAINED','pageSize':2 if kind=='inventory' else 50,'totalRows':len(rows),'terminalPageObserved':True,
        'pagination':[{'page':i+1,'rowCount':min(2,len(rows)-i*2) if kind=='inventory' else len(rows),'observedAt':at} for i,at in enumerate(times)],
        'capturedAt':times[-1],'collectionStartedAt':times[0],'materializedAt':'2026-10-05T08:00:00+00:00',
        'tenantWitness':{'visibleUsername':'SYNTHETIC TENANT','headerMatchesUsernameField':True,'observedAt':'2026-10-05T06:30:37+00:00','page':'https://m.seaya.cn/system/personalCenter/systemSetup'},
        'rawCsv':raw,'rawCsvSha256':hashlib.sha256(raw.encode()).hexdigest()}

def globals_pair():
    m=module('seaya_capture')
    rows=[dict(warehouse=w,seller_sku='0001',stock=5,allocated=0,inbound=0,frozen=0,available=5) for w in m.WAREHOUSES.values()]
    terminal=[dict(batch_id=r+'-TERMINAL',warehouse=w,total_units=3,status='COMPLETED') for r,w in m.WAREHOUSES.items()]
    inv=witness('inventory',rows);ib=witness('inbound',terminal)
    return m,inv,ib

def test_global_pages_keep_independent_clocks_and_local_partitions():
    m,iv,ib=globals_pair();inventory=m.capture_global_browser(iv);inbound=m.capture_global_browser(ib,inventory)
    assert inventory['capturedAt']!=inbound['capturedAt']
    assert inventory['collectionStartedAt']<inventory['capturedAt']
    assert not inventory['atomicSnapshot'] and inventory['evidence']['rowToPageBinding']=='NOT_RETAINED'
    assert len(inventory['evidence']['pagination'])==2 and all(len(v['pages'])==1 for v in inventory['regions'].values())
    assert all(v['totalRows']==0 for v in inbound['regions'].values())
    assert inbound['inventoryEvidenceDigest']==inventory['evidenceDigest']

@pytest.mark.parametrize('bad',['page','terminal','csv','atomic','warehouse','active','stock_inbound'])
def test_global_bad_evidence_cannot_be_relabelled_zero(bad):
    m,iv,ib=globals_pair()
    if bad=='page':iv['pagination'][1]['page']=3
    elif bad=='terminal':iv['terminalPageObserved']=False
    elif bad=='csv':iv['rawCsv']+='unbound'
    elif bad=='atomic':iv['atomicSnapshot']=True
    elif bad=='warehouse':iv['rawCsv']=iv['rawCsv'].replace('MY8803','OTHER');iv['rawCsvSha256']=hashlib.sha256(iv['rawCsv'].encode()).hexdigest()
    elif bad=='active':ib['rawCsv']=ib['rawCsv'].replace('COMPLETED','IN_TRANSIT');ib['rawCsvSha256']=hashlib.sha256(ib['rawCsv'].encode()).hexdigest()
    elif bad=='stock_inbound':iv['rawCsv']=iv['rawCsv'].replace(',5,0,0,0,5',',5,0,1,0,5');iv['rawCsvSha256']=hashlib.sha256(iv['rawCsv'].encode()).hexdigest()
    with pytest.raises(ValueError):
        inventory=m.capture_global_browser(iv);m.capture_global_browser(ib,inventory)

@pytest.mark.parametrize('region,offset',[('MY',8),('TH',7)])
def test_31_transport_units_are_not_30_completed_date_units(region,offset):
    from domains.supply_chain_operations.order_demand import aggregate_shopee_orders,finalize_order_snapshot
    local=timezone(timedelta(hours=offset));cutoff=datetime(2026,10,5,13,35,tzinfo=local)
    orders=[{'order_sn':str(age),'order_status':'COMPLETED','create_time':int((cutoff-timedelta(days=age)).timestamp()),'item_list':[{'model_sku':'0001','model_quantity_purchased':units}]} for age,units in [(0,1),(1,2),(7,4),(8,8),(15,16),(16,32),(30,64)]]
    facts,evidence=aggregate_shopee_orders(orders,region)
    s=finalize_order_snapshot(facts,region=region,platform='Shopee',captured_at=cutoff,days=31,evidence=evidence,window_policy='completed_local_calendar_30_v1')
    f=s['facts']['0001'];assert f['units']==127 and f['recent30Units']==126
    assert f['trendDecision']['units']=={'last7':6,'days8To15':24,'days16To30':96}
    assert s['demandWindow']['startAt'].startswith('2026-09-05T00:00:00') and s['demandWindow']['endAtExclusive'].startswith('2026-10-05T00:00:00')
    assert f['recent30Units']/30!=f['units']/30

def order_source(p,s):
    pull=module('pull_order_demand');targets={r:{'tiktok_shop_id':'TK-ID-'+r,'tiktok_cipher':'TK-'+r,'shopee_shop_id':i+1} for i,r in enumerate(pull.REGIONS)}
    for r,t in targets.items():p['captured']['targets'][r].update(tiktok=t['tiktok_cipher'],shopee=str(t['shopee_shop_id']))
    def tk(endpoint,token,query,body):
        r=query['shop_cipher'][3:];rows=[v for v in s['orders']['regions'][r]['tiktok']['pages'][0]['rows'] if body['create_time_ge']<=v['create_time']<body['create_time_lt']]
        return {'code':0,'data':{'orders':rows,'next_page_token':''}}
    def sp(endpoint,sid,token,params):
        r=pull.REGIONS[sid-1];rows=s['orders']['regions'][r]['shopee']['pages'][0]['rows']
        if endpoint==pull.SHOPEE_ORDER_DETAIL:return {'response':{'order_list':[v for v in rows if v['order_sn'] in params['order_sn_list'].split(',')]}}
        return {'response':{'order_list':[{'order_sn':v['order_sn']} for v in rows if params['time_from']<=v['create_time']<params['time_to']],'more':False,'next_cursor':''}}
    at=s['orders']['capturedAt'];source=pull.capture_orders(targets=targets,cutoff=at,days=31,source_id='synthetic-buffered-orders',started_at=at,materialized_at=at,sessions={r:{'tiktok':'SYNTHETIC','shopee':'SYNTHETIC'} for r in targets},requesters={'tiktok':tk,'shopee':sp})
    page=next(page for block in source['regions']['MY']['tiktok']['blocks'] for page in block['pages'] if any(v['status']=='COMPLETED' for v in page['rows']))
    row=next(v for v in page['rows'] if v['status']=='COMPLETED');item={'seller_sku':'','product_id':'SYNTHETIC PRODUCT','sku_id':'SYNTHETIC VARIANT'};row['line_items'].append(item);pull._seal_receipt(page)
    source.update(schema='supply-chain-captured-source/v3',demandWindowPolicy='completed_local_calendar_30_v1',mappingExceptions=[{'country':'MY','platform':'tiktok','orderId':row['id'],'lineIndex':1,'itemSha256':module('captured_refresh').digest(item),'reason':'UNRESOLVED_SELLER_SKU'}])
    return source

def test_actual_stage_preserves_unknown_line_and_continues_known_quantity(project):
    root,a,p,s=captured_inputs(project);source=order_source(p,s);resave(a,p,'orders',source)
    result,stage=cli(root,a,'--captured-stage');assert result.returncode==0,stage
    preview=json.loads((a/'captured-stages'/stage['stage_digest']/'preview.json').read_text())
    data=preview['data'];assert data['countries']['MY'][0]['channels']['tiktok']['recent30Units']==1
    assert not data['config']['MY']['orderMappingComplete'] and data['config']['TH']['orderMappingComplete']
    assert data['orderMappingExceptions']==[{**{k:v for k,v in e.items() if k!='orderId'},'orderRefSha256':module('captured_refresh').digest(e['orderId'])} for e in source['mappingExceptions']]
    assert data['sourceReadiness']['executionAuthority'] is False
    assert len(source['regions']['MY']['tiktok']['blocks'][-1]['pages'])>=1
    source['mappingExceptions'][0]['itemSha256']='0'*64;resave(a,p,'orders',source)
    result,value=cli(root,a,'--captured-stage');assert result.returncode!=0 and 'exceptions differ' in value['detail']

def test_global_browser_pair_enters_existing_stage(project):
    root,a,p,s=captured_inputs(project);m,iv,ib=globals_pair()
    inventory=m.capture_global_browser(iv);inbound=m.capture_global_browser(ib,inventory)
    resave(a,p,'inventory',inventory);resave(a,p,'inbound',inbound)
    result,stage=cli(root,a,'--captured-stage');assert result.returncode==0,stage
    preview=json.loads((a/'captured-stages'/stage['stage_digest']/'preview.json').read_text())
    assert preview['data']['capturedRefreshSources']['inventory']['atomicSnapshot'] is False
    assert preview['inbound']['capturedAt']=='2026-10-05T05:29:17+00:00'
