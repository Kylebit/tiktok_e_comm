import importlib.util,json,copy,hashlib
from pathlib import Path
import pytest
from test_u04_refresh import project,cli,write,git,ROOT
from test_u04_captured_refresh import captured_inputs,resave
SCRIPTS=ROOT/'domains/supply_chain_operations/skills/manage-seaya-replenishment/scripts'

@pytest.fixture(autouse=True)
def offline_guard(monkeypatch):
    import socket,sys
    def denied(*args,**kwargs):raise AssertionError('U04-K network/auth/DB forbidden')
    monkeypatch.setattr(socket.socket,'connect',denied)
    monkeypatch.setattr(socket,'create_connection',denied)
    class Guard:
        def find_spec(self,fullname,path=None,target=None):
            if fullname.startswith(('core.auth','core.config','modules.shopee.auth','sqlite3')):denied()
    guard=Guard();sys.meta_path.insert(0,guard)
    yield
    sys.meta_path.remove(guard)
def module():
    s=importlib.util.spec_from_file_location('seaya_capture',SCRIPTS/'seaya_capture.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m

def packet(m,rows,scope,at):
    page={'id':'p1','next':None,'observedAt':at,'rows':rows}
    page['sha256']=m.digest(page)
    return {'scope':scope,'coverage':{'kind':'observed_full_query','reference':'synthetic-full-scope-evidence','totalRows':len(rows),'pageIds':['p1'],'terminalPage':'p1'},'pages':[page]}

def bundles(m,project):
    root,a,p,s=captured_inputs(project);at=s['inventory']['capturedAt'];base={'schema':'seaya-capture-evidence/v1','tenantId':'synthetic-tenant','sourceId':'synthetic-evidence','capturedAt':at,'collectionStartedAt':at,'materializedAt':at,'regions':{}}
    inv={**copy.deepcopy(base),'kind':'inventory'};ib={**copy.deepcopy(base),'kind':'inbound'}
    for r,w in m.WAREHOUSES.items():
        inv['regions'][r]=packet(m,s['inventory']['regions'][r]['pages'][0]['rows'],{'warehouse':w,'selection':'all_inventory'},at)
        old=s['inbound']['regions'][r]['pages'][0]['rows'][0]
        row={'batch_id':old['batchId'],'country':r,'warehouse':w,'status':'IN_TRANSIT','created_at':old['createdAt'],'estimated_anchor_at':old['estimatedAnchorAt'],'domestic_inbound_at':None,'total_units':old['totalUnits'],'transport_days':7,'expected_sellable_date':old['estimatedSellableDate']}
        listing=packet(m,[row],{'warehouse':w,'selection':'all_in_transit_batches'},at)
        detail=packet(m,[{'box_no':'1','seller_sku':'0001','quantity':3}],{'warehouse':w,'batch_id':old['batchId'],'selection':'all_batch_details'},at)
        detail['status']='DETAIL_COMPLETE';listing['details']={old['batchId']:detail};ib['regions'][r]=listing
    return root,a,p,s,inv,ib

def test_complete_evidence_uses_actual_stage(project):
    m=module();root,a,p,s,inv,ib=bundles(m,project)
    inventory=m.capture(inv);inbound=m.capture(ib,inventory)
    resave(a,p,'inventory',inventory);resave(a,p,'inbound',inbound)
    result,stage=cli(root,a,'--captured-stage');assert result.returncode==0,stage
    assert stage['state']=='STAGED' and not (a/'captured-apply.json').exists()
    preview=json.loads((a/'captured-stages'/stage['stage_digest']/'preview.json').read_text(encoding='utf-8'))
    assert preview['data']['countries']['MY'][0]['inventory']['available']==5


def resign(m, value):
    for page in value['pages']:
        page['sha256']=m.digest({k:v for k,v in page.items() if k!='sha256'})


@pytest.mark.parametrize('bad', ['missing_page','terminal','total_only','wrong_warehouse','missing_country','row_warehouse','old_inventory','conflict','negative','tenant','missing_detail','old_detail','detail_pending','batch_total','inventory_total','duplicate_detail','obsolete_th','zero_original','altered_normalization','altered_evidence'])
def test_bad_evidence_cannot_stage_or_apply(project,bad):
    m=module();root,a,p,s,inv,ib=bundles(m,project)
    inventory=m.capture(inv);inbound=m.capture(ib,inventory)
    vi=inventory['evidence'];vb=inbound['evidence'];ri=vi['regions']['MY'];rb=vb['regions']['MY'];detail=next(iter(rb['details'].values()))
    if bad=='missing_page':ri['coverage']['pageIds'].append('missing')
    elif bad=='terminal':ri['coverage']['terminalPage']='missing'
    elif bad=='total_only':ri['coverage'].pop('kind')
    elif bad=='wrong_warehouse':ri['scope']['warehouse']='TH8806'
    elif bad=='missing_country':vi['regions'].pop('PH')
    elif bad=='row_warehouse':ri['pages'][0]['rows'][0]['warehouse']='PH8807'
    elif bad=='old_inventory':ri['pages'][0]['rows'][0]['captured_at']='2020-01-01T00:00:00+00:00'
    elif bad=='conflict':
        row=copy.deepcopy(ri['pages'][0]['rows'][0]);row['available']+=1;ri['pages'][0]['rows'].append(row);ri['coverage']['totalRows']+=1
    elif bad=='negative':ri['pages'][0]['rows'][0]['available']=-1
    elif bad=='tenant':vb['tenantId']='another-tenant'
    elif bad=='missing_detail':rb['details']={}
    elif bad=='old_detail':detail['pages'][0]['observedAt']='2020-01-01T00:00:00+00:00'
    elif bad=='detail_pending':detail['status']='PENDING'
    elif bad=='batch_total':rb['pages'][0]['rows'][0]['total_units']=4
    elif bad=='inventory_total':
        rb['pages'][0]['rows'][0]['total_units']=4;detail['pages'][0]['rows'][0]['quantity']=4
    elif bad=='duplicate_detail':
        detail['pages'][0]['rows']*=2;detail['coverage']['totalRows']=2;rb['pages'][0]['rows'][0]['total_units']=6
    elif bad=='obsolete_th':vb['regions']['TH']['details']['THSL4038-60638']=copy.deepcopy(detail)
    elif bad=='zero_original':
        detail['pages'][0]['rows'][0].update(quantity=0,original_quantity=3)
    elif bad=='altered_normalization':inventory['regions']['MY']['pages'][0]['rows'][0]['available']=999
    elif bad=='altered_evidence':ri['pages'][0]['sha256']='0'*64
    if bad!='altered_evidence':resign(m,ri)
    resign(m,rb);resign(m,detail)
    # Recompute the outer digest/normalization for bad evidence where possible;
    # rejection must be substantive, not just a stale envelope checksum.
    try:inventory=m.capture(vi)
    except ValueError:pass
    else:
        if bad=='altered_normalization':inventory['regions']['MY']['pages'][0]['rows'][0]['available']=999
    try:inbound=m.capture(vb,inventory)
    except ValueError:pass
    resave(a,p,'inventory',inventory);resave(a,p,'inbound',inbound)
    result,body=cli(root,a,'--captured-stage')
    assert result.returncode!=0,body
    assert not (a/'captured-apply.json').exists()
    assert not (a/'applied'/'data.js').exists()
    assert not list((a/'captured-stages').glob('*/stage.json'))


def test_complete_empty_th_does_not_restore_old_batch(project):
    m=module();root,a,p,s,inv,ib=bundles(m,project);at=inv['capturedAt']
    inv['regions']['TH']=packet(m,[],{'warehouse':'TH8806','selection':'all_inventory'},at)
    ib['regions']['TH']=packet(m,[],{'warehouse':'TH8806','selection':'all_in_transit_batches'},at)
    ib['regions']['TH']['details']={}
    inventory=m.capture(inv);inbound=m.capture(ib,inventory)
    resave(a,p,'inventory',inventory);resave(a,p,'inbound',inbound)
    result,stage=cli(root,a,'--captured-stage');assert result.returncode==0,stage
    preview=json.loads((a/'captured-stages'/stage['stage_digest']/'preview.json').read_text(encoding='utf-8'))
    assert preview['inbound']['regions']['TH']['batches']==[]
    assert preview['data']['countries']['TH'][0]['inventory']['inbound']==0


def test_multiple_pages_identical_inventory_duplicate(project):
    m=module();root,a,p,s,inv,ib=bundles(m,project)
    ri=inv['regions']['MY'];page=copy.deepcopy(ri['pages'][0]);page['id']='p2';ri['pages'][0]['next']='p2';ri['pages'].append(page)
    ri['coverage'].update(pageIds=['p1','p2'],terminalPage='p2',totalRows=2);resign(m,ri)
    with pytest.raises(ValueError,match='BLOCKED_INVENTORY'):
        m.capture(inv)


def test_materialization_does_not_advance_source_clock(project):
    m=module();root,a,p,s,inv,ib=bundles(m,project)
    for source in (inv,ib):source['materializedAt']='2030-01-01T00:00:00+00:00'
    inventory=m.capture(inv);inbound=m.capture(ib,inventory)
    resave(a,p,'inventory',inventory);resave(a,p,'inbound',inbound)
    result,stage=cli(root,a,'--captured-stage');assert result.returncode==0,stage
    preview=json.loads((a/'captured-stages'/stage['stage_digest']/'preview.json').read_text(encoding='utf-8'))
    assert preview['data']['snapshotDate']==inv['capturedAt'][:10]
    assert preview['data']['capturedRefreshSources']['inventory']['materializedAt'].startswith('2030')
    assert preview['inbound']['regions']['PH']['batches'][0]['detailObservedAt']!=ib['materializedAt']


def test_same_box_distinct_skus_and_detail_pages(project):
    m=module();root,a,p,s,inv,ib=bundles(m,project)
    ri=inv['regions']['PH'];second=copy.deepcopy(ri['pages'][0]['rows'][0]);second.update(seller_sku='0002',inbound=2)
    ri['pages'][0]['rows'].append(second);ri['coverage']['totalRows']=2;resign(m,ri)
    listing=ib['regions']['PH'];listing['pages'][0]['rows'][0]['total_units']=5;resign(m,listing)
    detail=next(iter(listing['details'].values()));second=copy.deepcopy(detail['pages'][0]);second.update(id='p2',rows=[{'box_no':'1','seller_sku':'0002','quantity':2}])
    detail['pages'][0]['next']='p2';detail['pages'].append(second);detail['coverage'].update(pageIds=['p1','p2'],terminalPage='p2',totalRows=2);resign(m,detail)
    inbound=m.capture(ib,m.capture(inv))
    assert inbound['regions']['PH']['pages'][0]['rows'][0]['skuQuantities']=={'0001':3,'0002':2}
    detail['pages'].pop()
    with pytest.raises(ValueError,match='missing page'):m.capture(ib,m.capture(inv))


@pytest.mark.parametrize('bad',['empty_without_proof','missing_next','reversed_clocks','mixed_v1'])
def test_additional_evidence_boundaries(project,bad):
    m=module();root,a,p,s,inv,ib=bundles(m,project)
    if bad=='mixed_v1':
        resave(a,p,'inventory',m.capture(inv));result,body=cli(root,a,'--captured-stage')
        assert result.returncode!=0 and 'versions must match' in body['detail']
        assert not (a/'captured-apply.json').exists()
        return
    value=inv['regions']['TH']
    if bad=='empty_without_proof':value['pages'][0]['rows']=[];value['coverage']={'totalRows':0}
    elif bad=='missing_next':value['pages'][0].pop('next')
    else:inv['collectionStartedAt']='2020-01-01T00:00:00+00:00'
    resign(m,value)
    with pytest.raises(ValueError):m.capture(inv)


def seed_module():
    s=importlib.util.spec_from_file_location('extract_current_seed',SCRIPTS/'extract_current_seed.py');m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m


@pytest.mark.parametrize('case',['preserve','occupied','missing_image','traversal','source_change'])
def test_seed_copy_is_exact_and_never_overwrites(project,case,monkeypatch):
    root,a,p,s=captured_inputs(project);m=seed_module();src=a/'applied';dest=a/'seed-copy'
    seed=json.loads((a/'seed.json').read_text(encoding='utf-8'));seed['manualExtra']={'nested':[0,False,'保留']};seed['snapshotDate']='2020-01-01'
    if case=='missing_image':seed['countries']['MY'][0]['image']='assets/missing.jpg'
    if case=='traversal':seed['countries']['MY'][0]['image']='assets/../../elsewhere.jpg'
    raw=(m.PREFIX+json.dumps(seed,ensure_ascii=False)+';\n').encode('utf-8');(src/'data.js').write_bytes(raw)
    (src/'inbound-plan.js').write_bytes(b'window.SUPPLY_CHAIN_INBOUND_PLAN = { old: true };')
    if case=='occupied':dest.mkdir();(dest/'existing').write_bytes(b'keep')
    if case=='source_change':
        original=m.inventory;calls=[]
        def drift(path):
            result=original(path);calls.append(path)
            if len(calls)==2:result['data.js']='changed'
            return result
        monkeypatch.setattr(m,'inventory',drift)
    if case!='preserve':
        with pytest.raises(ValueError):m.extract(src,dest)
        assert not (dest/'manifest.json').exists()
    else:
        report=m.extract(src,dest)
        assert (dest/'data.js').read_bytes()==raw
        assert json.loads((dest/'seed.json').read_text(encoding='utf-8'))==seed
        assert report['snapshotDate']=='2020-01-01' and report['state']=='COPIED_NOT_REFRESHED'
        assert m.inventory(src)==m.inventory(dest)
    assert (src/'data.js').read_bytes()==raw
