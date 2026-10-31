"""Real source-action projection preserves calculator evidence and exact sale."""
from copy import deepcopy
import pytest
from shared_platform.publication_discount_terms import apply_source_terms,source_terms,frozen_quote
from shared_platform.publication_r3_image_bridge import _frozen_store_prices
from test_publication_gallery_preparation_metadata import source
from test_round1_workspace_freeze import live
from test_round1_auto_freeze import public_settings


def inputs():
    source_quote={'target_key':'lh_ph','currency':'PHP','list_price':441,'discount_reserve_pct':35,
        'sale_after_discount_local':'286.65'}
    target={'role':'derived_listing','source':{'target_key':'lh_ph'},'sku_prices':[
        {'model_sku':'0031','variant_key':'blue','label':'Blue','source':source_quote,
            'derived_preview':{'local_original_price':441,'source_currency':'PHP','global_original_price_cny':'52.038'}}]}
    return {'target_pricing':{'shopee:PH':target},'selected_store_prices':[source_quote]}, {
        'review':{'publication_discounts_by_target':{'shopee:PH':30}}}


def test_actual_action_input_survives_r1_and_r3_without_rewriting_original_reserve():
    pricing,state=inputs();original=deepcopy(pricing)
    apply_source_terms(pricing,state,['shopee:PH'])
    original_quote=original['target_pricing']['shopee:PH']['sku_prices'][0]
    actual=pricing['target_pricing']['shopee:PH']['sku_prices'][0]
    assert {k:v for k,v in actual.items() if k!='postpublish_discount'}==original_quote
    row=source()._safe_target_facts({'pricing_review':pricing},['shopee:PH'])[0]['price']['sku_prices'][0]
    assert row['postpublish_discount']['percent_off']==30
    assert row['postpublish_discount']['sale_price']=='308.7'
    assert row['calculation']['derived_preview']['global_original_price_cny']=='52.038'
    assert _frozen_store_prices([row])==[{'currency':'PHP','list_price':441,'discount_reserve_pct':30,'sale_after_discount':'308.7'}]


def test_missing_action_does_not_add_historical_default_or_change_original_serialization():
    pricing,state=inputs();original=deepcopy(pricing)
    apply_source_terms(pricing,{'review':{}},['shopee:PH'])
    assert pricing==original
    state['source']=deepcopy(state['review']);state['source']['publication_discounts_by_target']['shopee:PH']=29
    with pytest.raises(ValueError,match='SOURCE_REVIEW_CONFLICT'):source_terms(state)


@pytest.mark.parametrize('percent',[True,0,100,30.5,'30'])
def test_untyped_or_invalid_action_does_not_enter_candidate(percent):
    pricing,state=inputs();state['review']['publication_discounts_by_target']['shopee:PH']=percent
    with pytest.raises(ValueError,match='PERCENT_INVALID'):apply_source_terms(pricing,state,['shopee:PH'])


def test_deeper_action_cannot_reuse_original_profit_protection_or_target_scope():
    pricing,state=inputs();state['review']['publication_discounts_by_target']['shopee:PH']=40
    with pytest.raises(ValueError,match='EXCEEDS_PRICED_RESERVE'):apply_source_terms(pricing,state,['shopee:PH'])
    with pytest.raises(ValueError,match='TARGET_NOT_SELECTED'):apply_source_terms(pricing,state,['shopee:MY'])


@pytest.mark.parametrize('change',['sale','currency','list','percent','source'])
def test_frozen_action_requires_exact_original_quote(change):
    pricing,state=inputs();apply_source_terms(pricing,state,['shopee:PH'])
    action=pricing['target_pricing']['shopee:PH']['sku_prices'][0]['postpublish_discount']
    if change=='sale':action['sale_price']='309'
    elif change=='currency':action['currency']='THB'
    elif change=='list':action['list_price']='442'
    elif change=='percent':action['percent_off']=True
    else:action['source']='AN_OLD_PRODUCT_APPROVAL'
    with pytest.raises(ValueError):frozen_quote(action,amount=441,currency='PHP')


def test_real_persisted_override_cannot_reuse_old_reserve_to_freeze_discount(live,monkeypatch,tmp_path):
    """Block the real state/quote/R1 input before any preparation can freeze it."""
    import json
    from test_round1_auto_freeze import complete_source
    from shared_platform import release_control,publication_rounds
    complete_source(live)
    path=live['root']/'data/new_product_workbench'/f"{live['offer']}.json"
    state=json.loads(path.read_bytes())
    state['review']['target_price_overrides']={'lh_ph':{
        'list_price':1,'currency':'PHP','authority':'OFFICIAL_SAME_STORE_A_LINK',
        'evidence_digest':'sha256:'+'a'*64,'source_product_id':'123456789',
        'reason':'Owned original A-link price; old calculator reserve is not repriced.'}}
    state['review']['publication_discounts_by_target']={'tiktok:LH_PH':30}
    path.write_text(json.dumps(state),encoding='utf-8')
    with pytest.raises(ValueError,match='EXPLICIT_DISCOUNT_OVERRIDDEN_PRICE_UNPROTECTED'):
        dashboard=release_control.build_release_dashboard(offer_id=live['offer'])
        # Before the fix the actual producer creates this unprotected action
        # and its R1 projection accepts it. No real approval is fabricated here.
        row=source()._safe_target_facts(dashboard,['tiktok:LH_PH'])[0]['price']['sku_prices'][0]
        from decimal import Decimal
        assert row['amount']==1 and Decimal(row['postpublish_discount']['sale_price'])==Decimal('0.7')
    assert not (publication_rounds.report_dir(live['offer'])/'round1-approved-snapshot.json').exists()


@pytest.mark.parametrize('carrier',['quote','source','original'])
@pytest.mark.parametrize('field',['price_override','pricing_authority'])
def test_only_selected_overridden_quote_blocks_new_action(carrier,field):
    pricing,state=inputs()
    quote=pricing['target_pricing']['shopee:PH']['sku_prices'][0]
    row={'quote':quote,'source':quote['source'],'original':pricing['selected_store_prices'][0]}[carrier]
    row[field]={'list_price':441} if field=='price_override' else 'OFFICIAL_SAME_STORE_A_LINK'
    original=deepcopy(pricing)
    with pytest.raises(ValueError,match='OVERRIDDEN_PRICE_UNPROTECTED'):
        apply_source_terms(pricing,state,['shopee:PH'])
    assert pricing==original
    # Historical/explicitly nonmarketing inputs do not acquire a new gate.
    apply_source_terms(pricing,{'review':{}},['shopee:PH'])
    assert pricing==original
