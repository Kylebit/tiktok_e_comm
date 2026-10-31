"""Current frozen R1 calculator is consumable before final publication review."""
from copy import deepcopy
import pytest
from shared_platform.publication_r3_image_bridge import _frozen_store_prices
from test_publication_gallery_preparation_metadata import source

def price():return {'model_sku':'0031','amount':441,'currency':'PHP','calculation':{
    'contract_version':'publication-price-calculation/v2','inputs':{'discount_reserve_pct':30},
    'result':{'list_price':441,'sale_after_discount':308.7,'currency':'PHP'}}}

def test_current_v2_actual_calculation_fields_keep_one_complete_uniform_quote():
    row=price();other={**deepcopy(row),'model_sku':'0032'}
    assert _frozen_store_prices([row,other])==[{'currency':'PHP','list_price':441,'discount_reserve_pct':30,'sale_after_discount':308.7}]

def test_actual_r1_preserves_target_bound_derived_discount_without_borrowing_source_rate():
    module=source()
    preview={'pricing_review':{'target_pricing':{'shopee:PH':{'role':'derived_listing',
        'sku_prices':[{'model_sku':'0031','label':'Blue','discount_reserve_pct':30,'sale_after_discount':'308.7',
            'source':{'discount_reserve_pct':35,'list_price':441,'currency':'PHP'},
            'derived_preview':{'local_original_price':441,'source_currency':'PHP','global_original_price_cny':'52.038'}}]}}}}
    facts=module._safe_target_facts(preview,['shopee:PH'])
    quote=facts[0]['price']['sku_prices'][0]
    assert quote['calculation']['discount_rate_percent']==30
    assert quote['calculation']['derived_preview']['global_original_price_cny']=='52.038'
    assert _frozen_store_prices([quote])[0]['discount_reserve_pct']==30
    del preview['pricing_review']['target_pricing']['shopee:PH']['sku_prices'][0]['discount_reserve_pct']
    assert _frozen_store_prices(module._safe_target_facts(preview,['shopee:PH'])[0]['price']['sku_prices'])==[]

def test_actual_r1_unwraps_master_source_before_safe_calculation():
    module=source()
    preview={'pricing_review':{'target_pricing':{'tiktok:MX':{'role':'master_listing',
        'sku_prices':[{'model_sku':'0031','label':'Blue','source':{'region':'MX','list_price':100,'currency':'MXN',
            'discount_reserve_pct':35,'sale_after_discount_local':65,'header_meta':{'discount_reserve_pct':35}}}]}}}}
    row=module._safe_target_facts(preview,['tiktok:MX'])[0]['price']['sku_prices'][0]
    assert row['calculation']['inputs']['discount_reserve_pct']==35
    assert row['calculation']['result']['sale_after_discount']==65

@pytest.mark.parametrize('damage',['no-rate','wrong-sale','wrong-list','wrong-currency','boolean','nonfinite'])
def test_unknown_or_conflicting_frozen_quote_never_uses_historical_discount(damage):
    row=price();c=row['calculation']
    if damage=='no-rate':del c['inputs']['discount_reserve_pct']
    elif damage=='wrong-sale':c['result']['sale_after_discount']=309
    elif damage=='wrong-list':c['result']['list_price']=442
    elif damage=='wrong-currency':c['result']['currency']='THB'
    elif damage=='boolean':c['inputs']['discount_reserve_pct']=True
    else:c['inputs']['discount_reserve_pct']='NaN'
    assert _frozen_store_prices([row])==[]
