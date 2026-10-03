"""Synthetic inputs exercise actual monthly builder and source acceptance."""
from datetime import date
from pathlib import Path
from types import SimpleNamespace
import hashlib
import json

import pytest

from domains.data_operations.profit_settlement.shared_inputs import CostSnapshot, FxSnapshot
from domains.data_operations.profit_settlement.tiktok import build_monthly_report
from domains.data_operations.profit_settlement.tiktok_coverage import build_coverage
from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform.workbench_profit_adapter import validate_reports, adapter


def make_bundle(root, shop='shop-MY'):
    root.mkdir(parents=True, exist_ok=True)
    def write(name, doc):
        path = root / name
        raw = json.dumps(doc, sort_keys=True, ensure_ascii=False).encode()
        path.write_bytes(raw)
        return {'path': str(path), 'sha256': hashlib.sha256(raw).hexdigest()}
    cost_records = {'0001': {'unit_cost_cny': '2', 'version': '2026-07-cost', 'effective_at': '2026-07-01T00:00:00+08:00', 'source': 'synthetic historical invoice'}}
    costs = CostSnapshot.from_mapping(cost_records)
    fx = FxSnapshot.from_mapping({'MYR':'1.5'}, source='synthetic FX source', as_of='2026-08-31T00:00:00+08:00')
    source_orders = [{'order_id':'o1','shop_id':shop,'order_created_at':'2026-08-01T10:00:00+08:00',
        'order_status':'COMPLETED','settlement_status':'settled','net_settlement_amount':'10','settled_at':'2026-08-15T10:00:00+08:00'}]
    checksum = hashlib.sha256(json.dumps(source_orders,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()
    settlement = {'schema_version':'settlement-evidence/v1','status':'ready','platform':'tiktok','site':'MY','shop_id':shop,
        'orders':source_orders,'snapshot_id':'tiktok-settlement:'+checksum,'checksum':checksum,'issues':[]}
    coverage = build_coverage(orders=source_orders,settled_order_ids={'o1'},start=date(2026,8,1),end=date(2026,8,31),as_of=date(2026,9,9),settlement_snapshot_id=settlement['snapshot_id'],site='MY',timezone_name='Asia/Kuala_Lumpur')
    ad_identity={'platform':'tiktok','site':'MY','shop_id':shop,'period':{'start':'2026-08-01','end':'2026-08-31'}}
    ad_source = write('original-ads.json',{'synthetic':True,'actual_advertising_cny':'1','currency':'CNY',**ad_identity})
    ads = {'mode':'actual','platform':'tiktok','site':'MY','shop_id':shop,'period':{'start':'2026-08-01','end':'2026-08-31'},
        'total_cny':'1','source':'synthetic actual monthly ads','as_of':'2026-09-01T00:00:00+08:00','snapshot_id':'actual-ads:fixture','source_file':ad_source,'amount_path':['actual_advertising_cny'],'currency_path':['currency']}
    row = {'shop_id':shop,'region':'MY','order_id':'o1','order_line_id':'l1','platform_sku':'p1','seller_sku':'660001','canonical_sku':'0001',
        'product_name':'Synthetic item','variant_name':'One','quantity':'1','currency':'MYR','net_settlement_amount':'10',
        'buyer_paid_product_amount':'12','buyer_cash_paid_product_amount':'12','settlement_status':'settled',
        'occurred_at':'2026-08-01T10:00:00+08:00','settled_at':'2026-08-15T10:00:00+08:00','source_snapshot_id':settlement['snapshot_id'],
        'fee_items':[],'fulfillment':{'mode':'cross_border','classification_rule':'tiktok_my_sst_nonzero_cross_border/v1','sst_local':'1','evidence_source':'synthetic finance SST'}}
    report = build_monthly_report([row],period_start='2026-08-01',period_end='2026-08-31',period_basis='order_created_at',costs=costs,fx=fx,actual_advertising=ads,code_version='profit-settlement-v1-tiktok-monthly-created-orders').payload()
    report['source'].update(all_non_cancelled_orders_settled=True,coverage_snapshot_id=coverage['snapshot_id'],settlement_observed_through='2026-09-09')
    assert report['status']=='ready'
    entry = {'platform':'tiktok','site':'MY','shop_id':shop,'report':write('report.json',report),
        'settlement':write('settlement.json',settlement),'coverage':write('coverage.json',coverage),'month_coverage':write('month-coverage.json',coverage),
        'orders_source':write('original-orders.json',{**ad_identity,'orders':[{**r,'lines':[{'order_line_id':'l1','seller_sku':'660001','canonical_sku':'0001','quantity':'1'}]} for r in source_orders],'complete':True,'next_cursor':None,'total_count':1,'source':'synthetic complete official export'}),
        'costs':write('costs.json',{'snapshot_id':costs.snapshot_id,'records':cost_records}), 'fx':write('fx.json',fx.payload()),'advertising':write('ads.json',ads)}
    manifest = {'schema_version':'profit-verification-manifest/v1','reports':[entry]}
    manifest_ref = write('manifest.json',manifest)
    scope = {'month':'2026-08','platforms':['tiktok'],'sites':['MY'],'shops':[shop]}
    return {'root':root,'write':write,'report':report,'entry':entry,'manifest':manifest,'paths':[entry['report']['path'],manifest_ref['path']], 'scope':scope}


@pytest.fixture
def bundle(tmp_path): return make_bundle(tmp_path/'bundle')


def rewrite_report(bundle):
    bundle['entry']['report']=bundle['write']('report.json',bundle['report'])
    bundle['write']('manifest.json',bundle['manifest'])


def test_current_monthly_producer_valid_bundle(bundle):
    assert set(validate_reports(bundle['paths'],bundle['scope']))=={('tiktok','MY','shop-MY')}


@pytest.mark.parametrize('mutation',['invalid_date','wrong_shop','missing_ads','missing_cost','missing_fx','needs_review','estimated_ads','source_drift','cutoff_lie','missing_manifest','missing_order','late_cost'])
def test_false_completeness_rejected(bundle,mutation):
    report=bundle['report']
    if mutation=='invalid_date': report['period']['end']='2026-08-99'
    if mutation=='wrong_shop': report['order_lines'][0]['identity']['shop_id']='another-shop'
    if mutation=='missing_ads': report['order_lines'][0]['advertising'].pop('source')
    if mutation=='missing_cost': report['order_lines'][0]['cost'].pop('source')
    if mutation=='missing_fx': report['order_lines'][0]['fx'].pop('checksum')
    if mutation=='needs_review': report['status']='needs_review'
    if mutation=='estimated_ads': report['calculation_kind']='realized_settlement_with_estimated_ads'
    if mutation=='cutoff_lie': report['source']['coverage_snapshot_id']='self-asserted'
    if mutation=='missing_order': report['order_lines'][0]['identity']['order_id']='other'
    if mutation=='late_cost': report['order_lines'][0]['cost']['effective_at']='2026-09-09T00:00:00+08:00'
    rewrite_report(bundle)
    if mutation=='source_drift': Path(bundle['entry']['coverage']['path']).write_text('{}')
    paths=bundle['paths'][:1] if mutation=='missing_manifest' else bundle['paths']
    with pytest.raises((ValueError,KeyError)):validate_reports(paths,bundle['scope'])


def test_first_unsettled_date_caps_report_not_filtered_subset(bundle):
    coverage=json.loads(Path(bundle['entry']['month_coverage']['path']).read_text())
    coverage['unsettled_non_cancelled_orders']=[{'order_id':'missing','order_created_at':'2026-08-20T10:00:00+08:00','order_status':'DELIVERED'}]
    # Even updating the file hash cannot make stale coverage counts valid.
    bundle['entry']['month_coverage']=bundle['write']('month-coverage.json',coverage)
    bundle['write']('manifest.json',bundle['manifest'])
    with pytest.raises(ValueError):validate_reports(bundle['paths'],bundle['scope'])


def test_missing_input_then_real_note_resume(bundle,tmp_path):
    from test_operations_profit_producer import inputs
    _, input_manifest, _, _ = inputs(tmp_path/'fixed-inputs')
    engine=WorkbenchEngine(tmp_path/'tasks.db',{'code_version':'fixture'})
    engine.register_executor('worker',['profit'],engine.release,ttl=300)
    task=engine.create({'template':'profit','source_key':'profit','scope':bundle['scope']})
    calls=[]
    class Bridge:
        def execute_monthly(self,task,output,notes):
            calls.append(notes)
            return {'status':'prepared','session_id':'synthetic-session','result':{'summary':'fixture','missing_inputs':[] if notes else ['提供广告账单'],'evidence_paths':[input_manifest['path']] if notes else []}}
    run=adapter(Bridge());profile=SimpleNamespace(data_root=tmp_path,environment='preview')
    token=engine.claim(task['task_id'],'worker')['lease_token'];run(engine,engine.get(task['task_id']),token,profile)
    waiting=engine.get(task['task_id']);assert waiting['execution_state']=='waiting_user'
    assert all(s['state']!='completed' for s in waiting['steps'])
    engine.user_action(task['task_id'],'provide-input',{'note':'已补充广告账单','action_id':waiting['required_action']['action_id']})
    token=engine.claim(task['task_id'],'worker')['lease_token'];run(engine,engine.get(task['task_id']),token,profile)
    assert calls==[[],['已补充广告账单']]
    assert engine.get(task['task_id'])['current_step']=='inputs'
    coverage=engine.get(task['task_id'])['steps'][0]['checkpoint']
    proof=json.loads(Path(coverage['producer_receipt']['path']).read_text())
    assert proof['executions'][0]['builder'].endswith('tiktok.build_monthly_report')
    assert 'report-policy.json' in proof['identity']['producer_sources']
    assert Path(coverage['producer_receipt']['path']).is_relative_to(tmp_path/'fixed-producer')
    while engine.get(task['task_id'])['execution_state']=='running':
        run(engine,engine.get(task['task_id']),token,profile)
    assert engine.get(task['task_id'])['execution_state']=='completed'


def test_existing_external_profit_task_cannot_start_again(tmp_path):
    engine=WorkbenchEngine(tmp_path/'tasks.db',{'code_version':'fixture'});engine.register_executor('worker',['profit'],engine.release)
    task=engine.create({'template':'profit','source_key':'existing05','scope':{'month':'2026-08'}})
    engine.attach_external(task['task_id'],external_id='existing-05',owner='05',observed_at='2026-09-09T00:00:00+08:00',observed_status='unknown')
    assert engine.claim(task['task_id'],'worker') is None


def test_advertising_original_amount_not_agent_total(bundle):
    ad=json.loads(Path(bundle['entry']['advertising']['path']).read_text())
    original=json.loads(Path(ad['source_file']['path']).read_text());original['actual_advertising_cny']='999'
    ad['source_file']=bundle['write']('original-ads.json',original)
    bundle['entry']['advertising']=bundle['write']('ads.json',ad)
    bundle['write']('manifest.json',bundle['manifest'])
    with pytest.raises(ValueError,match='frozen original amount'):validate_reports(bundle['paths'],bundle['scope'])


def test_prepared_result_reused_after_technical_validation_failure(bundle,tmp_path):
    engine=WorkbenchEngine(tmp_path/'tasks.db',{'code_version':'fixture'})
    engine.register_executor('worker',['profit'],engine.release,ttl=300)
    task=engine.create({'template':'profit','source_key':'retry','scope':bundle['scope']})
    calls=[]
    class Bridge:
        def execute_monthly(self,task,output,notes):
            calls.append(output)
            return {'status':'prepared','result':{'missing_inputs':[],'evidence_paths':bundle['paths'][:1]}}
    run=adapter(Bridge());profile=SimpleNamespace(data_root=tmp_path)
    token=engine.claim(task['task_id'],'worker')['lease_token']
    with pytest.raises(ValueError):run(engine,engine.get(task['task_id']),token,profile)
    # Same known response is revalidated even if the worker invokes the adapter again.
    with pytest.raises(ValueError):run(engine,engine.get(task['task_id']),token,profile)
    assert len(calls)==1


@pytest.mark.parametrize('mutation',['extra_shop','wrong_original_ads','omitted_unsettled_order'])
def test_independent_review_red_cases(bundle,mutation):
    if mutation=='extra_shop':bundle['scope']['shops'].append('shop-MY-SECOND')
    elif mutation=='wrong_original_ads':
        ad=json.loads(Path(bundle['entry']['advertising']['path']).read_text())
        original=json.loads(Path(ad['source_file']['path']).read_text());original['shop_id']='another-shop'
        ad['source_file']=bundle['write']('original-ads.json',original)
        bundle['entry']['advertising']=bundle['write']('ads.json',ad)
    else:
        original=json.loads(Path(bundle['entry']['orders_source']['path']).read_text())
        original['orders'].append({'order_id':'unsettled','order_created_at':'2026-08-20T10:00:00+08:00','order_status':'DELIVERED'})
        original['total_count']=2
        bundle['entry']['orders_source']=bundle['write']('original-orders.json',original)
    bundle['write']('manifest.json',bundle['manifest'])
    with pytest.raises(ValueError):validate_reports(bundle['paths'],bundle['scope'])


def test_two_shops_same_platform_site_are_separate_and_complete(tmp_path):
    one=make_bundle(tmp_path/'one');two=make_bundle(tmp_path/'two',shop='shop-MY-SECOND')
    one['manifest']['reports'].append(two['entry']);one['write']('manifest.json',one['manifest'])
    one['scope']['shops'].append('shop-MY-SECOND')
    assert len(validate_reports(one['paths']+[two['entry']['report']['path']],one['scope']))==2
    with pytest.raises(ValueError,match='actually validated'):validate_reports(one['paths'],one['scope'])


def test_original_sold_sku_and_quantity_contradiction_rejected(bundle):
    original=json.loads(Path(bundle['entry']['orders_source']['path']).read_text())
    original['orders'][0]['lines']=[{'order_line_id':'l1','seller_sku':'660999','canonical_sku':'0999','quantity':9}]
    bundle['entry']['orders_source']=bundle['write']('original-orders.json',original)
    bundle['write']('manifest.json',bundle['manifest'])
    with pytest.raises(ValueError,match='SKU/quantity differs'):validate_reports(bundle['paths'],bundle['scope'])
