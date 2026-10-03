from copy import deepcopy
import importlib.util
from pathlib import Path
import pytest

@pytest.fixture
def client():
    path=Path(__file__).resolve().parents[1]/'skills/prepare-product-publication/scripts/prepare_product_publication.py'
    spec=importlib.util.spec_from_file_location('r1_material_client',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module

def preview():
    return {'ok':True,'revision':7,'publication_scope':{'selected_labels':['tiktok:LH_MY']},
        'product':{'title':'Fixture','seller_sku_candidate':'0988','cost_cny':8,'weight_kg':0.2,
        'package_cm':[20,20,3],'source_skus':[{'key':'a','model_sku':'0988','label':'20 cm',
        'commercial_facts':{'cost_cny':8,'weight_kg':0.2,'package_cm':[20,20,3]}}]},
        'pricing_review':{'target_pricing':{'tiktok:LH_MY':{'list_price':20,'currency':'MYR'}}}}

def prepare(client,p):
    return client.prepare_offer(offer_id='3956742887',requested_targets=['tiktok:LH_MY'],
        preview_builder=lambda _:p,image_execution_plan={'schema_version':'first-review-image-plan/v1',
        'status':'PROPOSED','source_actions':[],'generated_assets':[],
        'summary':{'translation_positions':[],'localized_output_count':0,'net_new_output_count':0,
        'paid_generation_required':False}},candidate_plan={
        'schema_version':'first-review-candidate-plan/v1','target_candidates':[{'target':'tiktok:LH_MY',
        'category':{'status':'PROPOSED','candidate':'Decoration','authority':'Fixture proposal',
        'evidence_digest':'sha256:'+'a'*64,'note':'Pending official review'},
        'copy':{'status':'PROPOSED','language':'ms','title':'Fixture','description':'Fixture description',
        'specification_name':'Size','variants':[{'seller_sku':'0988','display_name':'20 cm'}]}}],
        'content_group_options':[{'id':'livelyhive-sea','label':'LivelyHive SEA',
        'description':'Selected brand','recommended':True}]})

def test_per_sku_parcels_are_exposed_without_mutating_preview(client):
    p=preview();before=deepcopy(p);result=prepare(client,p)
    assert result['status']=='FIRST_REVIEW_READY'
    assert result['product_facts']['parcel_fact_mode']=='PER_SKU'
    assert result['product_facts']['per_sku_parcel_facts'][0]['seller_sku']=='0988'
    assert p==before

@pytest.mark.parametrize('bad',[None,0,-1,float('nan'),float('inf'),True])
def test_incomplete_or_invalid_parcel_cannot_be_ready(client,bad):
    p=preview();p['product']['weight_kg']=bad
    p['product']['source_skus'][0]['commercial_facts']['weight_kg']=bad
    result=prepare(client,p)
    assert result['status']=='DECISION_REQUIRED'
    assert result['product_facts']['parcel_fact_mode']=='INCOMPLETE'

def test_partial_per_sku_facts_do_not_hide_behind_shared_defaults(client):
    p=preview();p['product']['source_skus'][0]['commercial_facts']['weight_kg']=None
    result=prepare(client,p)
    assert result['status']=='DECISION_REQUIRED'
    assert result['product_facts']['parcel_fact_mode']=='INCOMPLETE'

@pytest.mark.parametrize('bad',[None,0,-1,float('nan'),float('inf'),True])
def test_invalid_price_blocks_first_review(client,bad):
    p=preview();p['pricing_review']['target_pricing']['tiktok:LH_MY']['list_price']=bad
    assert prepare(client,p)['status']=='DECISION_REQUIRED'

def test_pricing_store_projection_preserves_formula_and_sku_amount(client):
    p=preview();p['pricing_review']['target_pricing']['tiktok:LH_MY']={
        'role':'master_listing','status':'CANDIDATE','store_prices':[{'region':'MY','list_price':20,
        'currency':'MYR','fees':{'goods_cost_local':5},'formula_parameters':{'commission_rate':10}}],
        'sku_prices':[{'model_sku':'0988','label':'20 cm','list_price':20,'currency':'MYR'}]}
    result=prepare(client,p);price=result['targets'][0]['price']
    assert price['amount']==20 and price['currency']=='MYR'
    assert price['sku_prices'][0]['model_sku']=='0988'
    assert price['calculation']['inputs']['goods_cost_local']==5
    assert price['calculation']['inputs']['commission_rate_pct']==10

def test_common_draft_has_no_market_price_requirement(client):
    blockers=client._material_review_blockers(product_facts={'parcel_fact_mode':'SHARED'},
        targets=[{'target':'miaoshou:COMMON','price':{}}],image_plan={},requested_targets=['miaoshou:COMMON'])
    assert blockers==[]

def test_shared_parcel_fallback_only_without_variant_specific_facts(client):
    p=preview();p['product']['source_skus'][0].pop('commercial_facts')
    assert prepare(client,p)['product_facts']['parcel_fact_mode']=='SHARED'

def test_complete_variant_parcels_override_conflicting_shared_candidate(client):
    p=preview();p['product']['package_cm']=[1,2,3]
    facts=prepare(client,p)['product_facts']
    assert facts['parcel_fact_mode']=='PER_SKU' and facts['parcel_warnings']
    assert facts['per_sku_parcel_facts'][0]['package_cm']==[20,20,3]

@pytest.mark.parametrize('derived,amount,currency',[
    ({'local_original_price':20,'price_cny':30,'source_currency':'MYR'},20,'MYR'),
    ({'amount':20,'currency':'MYR'},20,'MYR'),
    ({'price_cny':30,'source_currency':'MYR'},30,'CNY'),
])
def test_derived_price_keeps_amount_and_currency_together(client,derived,amount,currency):
    p=preview();p['pricing_review']['target_pricing']['tiktok:LH_MY']={'derived_preview':derived}
    price=client._safe_target_facts(p,['tiktok:LH_MY'])[0]['price']
    assert (price['amount'],price['currency'])==(amount,currency)
    assert price['calculation']['kind']=='DERIVED_PRICE_CANDIDATE'

@pytest.mark.parametrize('amount',[None,0,-1,float('inf'),True])
def test_bad_variant_price_cannot_hide_behind_valid_product_price(client,amount):
    p=preview();p['pricing_review']['target_pricing']['tiktok:LH_MY']['sku_prices']=[
        {'model_sku':'0988','label':'20 cm','list_price':amount,'currency':'MYR'}]
    assert prepare(client,p)['status']=='DECISION_REQUIRED'

def test_complete_variant_prices_do_not_require_one_aggregate_price(client):
    p=preview();row=p['pricing_review']['target_pricing']['tiktok:LH_MY'];row.pop('list_price')
    row['sku_prices']=[{'model_sku':'0988','label':'20 cm','list_price':20,'currency':'MYR'}]
    assert prepare(client,p)['status']=='FIRST_REVIEW_READY'

@pytest.mark.parametrize('variant',[False,True])
def test_derived_cny_amount_never_inherits_unrelated_outer_currency(client,variant):
    p=preview();value={'currency':'MYR','derived_preview':{'price_cny':30,'source_currency':'MYR'}}
    if variant:
        value.update(model_sku='0988',label='20 cm')
        p['pricing_review']['target_pricing']['tiktok:LH_MY']['sku_prices']=[value]
        price=client._safe_target_facts(p,['tiktok:LH_MY'])[0]['price']['sku_prices'][0]
    else:
        p['pricing_review']['target_pricing']['tiktok:LH_MY']=value
        price=client._safe_target_facts(p,['tiktok:LH_MY'])[0]['price']
    assert (price['amount'],price['currency'])==(30,'CNY')

def test_explicit_outer_price_keeps_its_own_currency(client):
    p=preview();p['pricing_review']['target_pricing']['tiktok:LH_MY'].update(
        list_price=20,currency='MYR',derived_preview={'price_cny':30})
    price=client._safe_target_facts(p,['tiktok:LH_MY'])[0]['price']
    assert (price['amount'],price['currency'])==(20,'MYR')
