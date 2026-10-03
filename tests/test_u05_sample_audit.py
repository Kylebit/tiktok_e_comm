from copy import deepcopy
import pytest
from test_u05_waterfall import waterfall_fixture
from test_u05_sku_evidence import resave,request,save,repin_catalog
from pathlib import Path
import sqlite3

def sample_fixture(folder,case='large'):
    p,s,i=waterfall_fixture(folder);entry=s['entries'][0];row=deepcopy(entry['samples']['rows'][0]);fee=deepcopy(entry['waterfall']['sample_fees'][0])
    rows=[];fees=[]
    for n in range(37):
        item=deepcopy(row);item.update(order_id=f'SYNTHETIC-AUDIT-{n:03d}',paid_product_amount='100',net_settlement_amount=str([20,62,100,0][n%4]),quantity=1)
        if case=='equal':item['net_settlement_amount']='100'
        rows.append(item);part=deepcopy(fee);part.update(order_id=item['order_id'],affiliate_state=['with','without','unknown'][n%3],components={});fees.append(part)
    entry['samples']['rows']=rows;entry['waterfall']['sample_fees']=fees
    if case=='empty':entry['samples']['rows']=[];entry['waterfall']['sample_fees']=[]
    if case=='unknown':p.pop('ad_rate')
    if case=='missing-fees':entry['waterfall'].pop('sample_fees')
    resave(p,s);return p,s,i

def test_all_37_samples_return_without_changing_estimator_population(tmp_path):
    p,s,i=sample_fixture(tmp_path);status,v=request(p,i);save(tmp_path/'response.json',v)
    assert status==200
    a=v['sample_audit'];assert a['counts']['captured']==37 and a['counts']['returned']==37
    assert len(a['rows'])==37 and len({r['row_key'] for r in a['rows']})==37
    assert v['estimate']['n']==37 and v['waterfall']['posterior']['all']['n']==37
    assert any(r['outlier'] is True and r['used_in_base_estimate'] for r in a['rows'])

def test_outlier_boundary_uses_existing_enriched_ratio_precision(tmp_path):
    p,s,i=sample_fixture(tmp_path);s['entries'][0]['samples']['rows'][0].update(paid_product_amount='10000',net_settlement_amount='999.9')
    resave(p,s);v=request(p,i)[1]
    assert v['sample_audit']['rows'][0]['outlier'] is False

def test_optional_sample_fees_omitted_equals_explicit_empty_at_bridge(tmp_path):
    p,s,i=sample_fixture(tmp_path,'missing-fees')
    status,omitted=request(p,i);save(tmp_path/'omitted-response.json',omitted)
    assert status==200
    s['entries'][0]['waterfall']['sample_fees']=[];resave(p,s)
    status,empty=request(p,i);save(tmp_path/'empty-response.json',empty)
    assert status==200
    assert omitted['estimate']==empty['estimate'] and omitted['estimate']['n']==37
    assert {k:v for k,v in omitted['waterfall'].items() if k!='sha256'}=={k:v for k,v in empty['waterfall'].items() if k!='sha256'}
    assert omitted['sample_audit']['rows']==empty['sample_audit']['rows']
    assert omitted['sample_audit']['counts']=={'captured':37,'returned':37,'settled_in_window':37,'eligible':37}
    assert all(r['affiliate_state']=='unknown' and r['used_in_base_estimate'] for r in omitted['sample_audit']['rows'])

@pytest.mark.parametrize('missing',['cost','fx','ad'])
def test_missing_model_inputs_do_not_hide_raw_samples(tmp_path,missing):
    p,s,i=sample_fixture(tmp_path)
    if missing=='cost':
        with sqlite3.connect(p['catalog_path']) as db:db.execute('DELETE FROM sku_costs')
        repin_catalog(p,s)
    elif missing=='fx':Path(p['fx_path']).unlink()
    else:p.pop('ad_rate')
    v=request(p,i)[1];a=v['sample_audit'];assert len(a['rows'])==37 and a['counts']['returned']==37
    assert all(r['profit_cny'] is None and r['profit_class']=='unknown' and not r['used_in_base_estimate'] for r in a['rows'])
    assert a['rows'][0]['paid_total_local']=='100' and a['rows'][0]['quantity']==1
    assert all(missing+'_missing' in r['missing_reasons'] for r in a['rows'])

@pytest.mark.parametrize('case',['date','duplicate','identity','paid','net','quantity','basis','scalar','source'])
def test_rejected_records_are_returned_with_reasons(tmp_path,case):
    p,s,i=sample_fixture(tmp_path);samples=s['entries'][0]['samples'];row=samples['rows'][0]
    if case=='date':row['settled_at']='2026-08-11T00:00:00+07:00'
    elif case=='duplicate':row['order_id']=samples['rows'][1]['order_id']
    elif case=='identity':row['identity']={**i,'shop_key':'other'}
    elif case=='paid':row['paid_product_amount']=None
    elif case=='net':row['net_settlement_amount']=float('nan')
    elif case=='quantity':row['quantity']=None
    elif case=='basis':row['net_basis']='unknown'
    elif case=='scalar':samples['rows'][0]=None
    else:samples['source']=''
    resave(p,s);status,v=request(p,i);save(tmp_path/'response.json',v)
    assert status==200 and v['estimate'] is None
    a=v['sample_audit'];assert a['counts']['captured']==a['counts']['returned']==37
    assert not a['rows'][0]['eligible'] and a['rows'][0]['missing_reasons'] and a['rows'][0]['profit_cny'] is None
    assert len({r['row_key'] for r in a['rows']})==37

def test_unsettled_rows_stay_in_capture_but_not_estimator(tmp_path):
    p,s,i=sample_fixture(tmp_path);s['entries'][0]['samples']['rows'][0]['status']='unsettled';s['entries'][0]['waterfall']['sample_fees'].pop(0)
    resave(p,s);v=request(p,i)[1];a=v['sample_audit']
    assert a['counts']=={'captured':37,'returned':37,'settled_in_window':36,'eligible':36}
    assert v['estimate']['n']==36 and not a['rows'][0]['used_in_base_estimate']

def test_sign_classes_and_explicit_affiliation_are_distinct(tmp_path):
    p,s,i=sample_fixture(tmp_path);a=request(p,i)[1]['sample_audit']
    assert [r['profit_class'] for r in a['rows'][:4]]==['negative','zero','positive','negative']
    assert [r['affiliate_state'] for r in a['rows'][:3]]==['with','without','unknown']
    assert a['rows'][3]['outlier'] is True

def test_empty_capture_is_zero_records_not_zero_profit(tmp_path):
    p,s,i=sample_fixture(tmp_path,'empty');v=request(p,i)[1]
    assert v['sample_audit']['counts']=={'captured':0,'returned':0,'settled_in_window':0,'eligible':0}
    assert v['sample_audit']['rows']==[] and v['estimate'] is None

def test_quantity_and_total_are_not_confused_with_per_item(tmp_path):
    p,s,i=sample_fixture(tmp_path);s['entries'][0]['samples']['rows'][0]['quantity']=2
    resave(p,s);r=request(p,i)[1]['sample_audit']['rows'][0]
    assert r['paid_total_local']=='100' and r['paid_per_item_local']=='50' and r['net_per_item_local']=='10'
    assert r['profit_cny']==-8.2
