from copy import deepcopy
import pytest
from test_u05_sku_evidence import sku_fixture,resave,request,save

FEES=('logistics_local','commission_local','transaction_local','extra_local','creator_local','affiliate_local','seller_tax_local','fixed_fee_local')

def waterfall_fixture(folder):
    p,s,i=sku_fixture(folder)
    parts=dict(zip(FEES,['10','20','5','20','10','5','5','5']))
    parts.update(goods_local='40',ad_local='55')
    prior={'sale_local':'250','components':parts,'include_tax':True,'extra_cap':{'uncapped_amount':'25','cap_amount':'20','extra_cap_hit':True}}
    without=deepcopy(prior);without['components'].update(creator_local='0',affiliate_local='0')
    records=[]
    for n,row in enumerate(s['entries'][0]['samples']['rows']):
        paid=float(row['paid_product_amount']);values=[.1,.1,.05,.05,.05,.05,.05,.05]
        if n==1:values=[.1,.2,.05,.05,0,0,.05,.05]
        records.append({'identity':i,'order_id':row['order_id'],'currency':'THB','affiliate_state':['with','without','unknown'][n],'components':{k:str(paid*v) for k,v in zip(FEES,values)}})
    s['entries'][0]['waterfall']={'identity':i,'currency':'THB','creator_affiliate_basis':'distinct_non_overlapping','source':'SYNTHETIC fee capture','version':'fees-1','as_of':'2026-08-10T00:00:00+07:00','valid_from':'2026-08-03T00:00:00+07:00','valid_to':'2026-08-10T00:00:00+07:00','prior':{'with_affiliate':prior,'no_affiliate':without},'sample_fees':records}
    resave(p,s);return p,s,i

def test_full_waterfall_uses_real_model_and_reconciles_fees_once(tmp_path):
    p,s,i=waterfall_fixture(tmp_path);status,v=request(p,i);save(tmp_path/'response.json',v)
    assert status==200
    w=v['waterfall']
    for scenario in ('with_affiliate','no_affiliate'):
        for field,value in s['entries'][0]['waterfall']['prior'][scenario]['components'].items():
            assert float(w['prior'][scenario]['components'][field])==float(value)
    assert w['prior']['with_affiliate']['estimate']['profit_cny']==15
    assert w['prior']['no_affiliate']['estimate']['profit_cny']==18
    assert w['prior']['with_affiliate']['extra_cap']['extra_cap_hit'] is True
    assert w['observed']['reconciliation']=='complete' and w['observed']['unexplained_delta_local']=='0'
    assert v['estimate']['profit_cny']==6 and w['posterior']['all']['estimate']['profit_cny']==6
    assert w['posterior']['unknown_affiliate_count']==1

def test_contradictory_no_affiliate_capture_is_rejected_not_zeroed(tmp_path):
    p,s,i=waterfall_fixture(tmp_path)
    s['entries'][0]['waterfall']['prior']['no_affiliate']['components']['affiliate_local']='5'
    resave(p,s);v=request(p,i)[1];save(tmp_path/'response.json',v)
    assert v['waterfall']['prior']['no_affiliate']['estimate'] is None
    assert v['waterfall']['prior']['no_affiliate']['status']=='invalid'

@pytest.mark.parametrize('case',['fee','scenario','cap'])
def test_partial_prior_does_not_fill_missing_parts(tmp_path,case):
    p,s,i=waterfall_fixture(tmp_path);w=s['entries'][0]['waterfall']
    if case=='fee':w['prior']['with_affiliate']['components'].pop('logistics_local')
    elif case=='scenario':w['prior'].pop('with_affiliate')
    else:w['prior']['with_affiliate'].pop('extra_cap')
    resave(p,s);v=request(p,i)[1]['waterfall'];r=v['prior']['with_affiliate']
    assert r['estimate'] is None and r['status'] in {'partial','missing'}
    assert v['prior']['no_affiliate']['estimate']['profit_cny']==18
    if case=='fee':assert r['components']['logistics_local'] is None and r['components']['commission_local']=='20'

@pytest.mark.parametrize('case',['identity','currency','stale','source','overlap','duplicate','fee_currency','fee_identity','nan'])
def test_invalid_waterfall_source_is_unknown(tmp_path,case):
    p,s,i=waterfall_fixture(tmp_path);w=s['entries'][0]['waterfall']
    if case=='identity':w['identity']={**i,'shop_key':'other'}
    elif case=='currency':w['currency']='MYR'
    elif case=='stale':w['valid_to']='2026-08-09T00:00:00+07:00'
    elif case=='source':w['source']=' '
    elif case=='overlap':w['creator_affiliate_basis']='same_fee_aliases'
    elif case=='duplicate':w['sample_fees'].append(deepcopy(w['sample_fees'][0]))
    elif case=='fee_currency':w['sample_fees'][0]['currency']='MYR'
    elif case=='fee_identity':w['sample_fees'][0]['identity']={**i,'variant_id':'other'}
    else:w['sample_fees'][0]['components']['extra_local']='NaN'
    resave(p,s);v=request(p,i)[1]['waterfall'];assert v['status']=='invalid' and v['observed'] is None and v['posterior'] is None

@pytest.mark.parametrize('case',['nan','cap','price','ad','cost'])
def test_conflicting_prior_keeps_other_scenario_independent(tmp_path,case):
    p,s,i=waterfall_fixture(tmp_path);r=s['entries'][0]['waterfall']['prior']['with_affiliate']
    if case=='nan':r['components']['extra_local']='Infinity'
    elif case=='cap':r['extra_cap']['extra_cap_hit']=False
    elif case=='price':r['sale_local']='300'
    elif case=='ad':r['components']['ad_local']='0'
    else:r['components']['goods_local']='0'
    resave(p,s);v=request(p,i)[1]['waterfall'];assert v['prior']['with_affiliate']['status']=='invalid'
    assert v['prior']['with_affiliate']['estimate'] is None and v['prior']['no_affiliate']['estimate']

def test_partial_fees_preserve_known_subtotal_without_naming_difference(tmp_path):
    p,s,i=waterfall_fixture(tmp_path);w=s['entries'][0]['waterfall'];w['sample_fees'][0]['components'].pop('logistics_local')
    resave(p,s);v=request(p,i)[1];o=v['waterfall']['observed']
    assert o['reconciliation']=='partial' and o['components']['logistics_local']=={'amount':None,'known_subtotal':'50','known_rows':2}
    assert o['unexplained_delta_local']=='10' and v['estimate']['profit_cny']==6
    assert not any(k in o['components'] for k in ('other_fee','unexplained_fee'))

def test_complete_but_unbalanced_fees_are_not_adjusted(tmp_path):
    p,s,i=waterfall_fixture(tmp_path);s['entries'][0]['waterfall']['sample_fees'][0]['components']['commission_local']='11'
    resave(p,s);o=request(p,i)[1]['waterfall']['observed'];assert o['reconciliation']=='mismatch' and o['unexplained_delta_local']=='-1'

def test_missing_affiliate_state_does_not_become_without(tmp_path):
    p,s,i=waterfall_fixture(tmp_path)
    for row in s['entries'][0]['waterfall']['sample_fees']:row['affiliate_state']='unknown'
    resave(p,s);v=request(p,i)[1]['waterfall']['posterior']
    assert v['unknown_affiliate_count']==3 and v['no_affiliate']['estimate'] is None and v['with_affiliate']['estimate'] is None and v['all']['estimate']

def test_no_fee_records_have_unknown_total_and_unknown_affiliation(tmp_path):
    p,s,i=waterfall_fixture(tmp_path);s['entries'][0]['waterfall']['sample_fees']=[]
    resave(p,s);w=request(p,i)[1]['waterfall']
    assert w['observed']['known_deductions_local'] is None
    assert all(v['amount'] is None and v['known_subtotal'] is None for v in w['observed']['components'].values())
    assert w['posterior']['unknown_affiliate_count']==3

def test_missing_model_input_preserves_actual_sample_count(tmp_path):
    p,s,i=waterfall_fixture(tmp_path);p.pop('ad_rate');w=request(p,i)[1]['waterfall']
    assert w['posterior']['all']['estimate'] is None and w['posterior']['all']['n']==3

def test_capture_without_waterfall_retains_old_estimate_and_explicit_gap(tmp_path):
    p,s,i=sku_fixture(tmp_path);v=request(p,i)[1]
    assert v['estimate']['profit_cny']==6 and v['waterfall']['status']=='missing'

def test_model_asymmetry_is_documented_without_global_formula_change(tmp_path):
    from modules.finance.sku_profit_model import PriorBreakdown,prior_profit_from_breakdown
    p,s,i=waterfall_fixture(tmp_path);r=s['entries'][0]['waterfall']['prior']['with_affiliate']
    bd=PriorBreakdown(sale_local=250,extra_cap_hit=True,**{k:float(v) for k,v in r['components'].items()})
    raw=prior_profit_from_breakdown(bd,include_creator=False,include_tax=True,fx=.2)
    difference=round(raw['est_settlement_local']-bd.goods_local-bd.ad_local-raw['profit_local'],2)
    save(tmp_path/'legacy-model-asymmetry.json',{'raw_model':raw,'net_less_goods_ads_minus_profit':difference,'reason':'nonzero affiliate with include_creator false; adapter rejects this capture'})
    assert difference==5
