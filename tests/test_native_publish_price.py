"""Frozen publication price consumption; no manufactured discount or grant."""
from copy import deepcopy
from dataclasses import replace

import pytest

from modules.products import release_adapters as adapters
from domains.channel_operations.release_executor import AdapterExecutionResult
from test_round1_workspace_freeze import live
from test_native_sole_final_service import _installed
from test_native_sole_final_continuation import _join


def test_real_native_decision_consumes_exact_frozen_publish_price_without_discount_evidence(live,monkeypatch,tmp_path):
    installed,market,*_=_installed(live,monkeypatch,tmp_path)
    original=adapters.production_adapter_registry()
    consumed=[]
    def no_provider(*args,**kwargs):
        pytest.fail('Only the closed owned submission seam may run')
    monkeypatch.setattr(adapters,'_tiktok_readback',no_provider)
    monkeypatch.setattr(adapters.auth,'access_token',no_provider)
    def accepted(payload,*,site):
        label='tiktok:'+site
        frozen=payload['r3_marketplace_binding']['route']['target_facts'][label]['price']['sku_prices']
        assert not payload['pricing']['selected_targets'][label].get('store_prices')
        with pytest.raises(RuntimeError,match='requires exactly one approved store price'):
            adapters._store_price(payload,label)
        price=adapters._tiktok_publish_price(payload,site)
        assert price['sku_prices']==[{'model_sku':row['model_sku'],
            'list_price':row['amount'],'currency':row['currency']} for row in frozen]
        assert price['list_price']==frozen[0]['amount'] and price['currency']==frozen[0]['currency']
        assert set(price)=={'sku_prices','list_price','currency'}
        for damage in ('amount','currency','sku','source','frozen'):
            changed=deepcopy(payload)
            projected=changed['pricing']['selected_targets'][label]
            if damage=='amount':projected['sku_prices'][0]['list_price']+=1
            elif damage=='currency':projected['sku_prices'][0]['currency']='USD'
            elif damage=='sku':projected['sku_prices'][0]['model_sku']='foreign-sku'
            elif damage=='source':projected['source']['region']='PH' if site=='LH_MY' else 'MY'
            else:changed['r3_marketplace_binding']['route']['target_facts'][label]['price']['sku_prices'][0]['amount']+=1
            with pytest.raises(RuntimeError,match='NATIVE_TARGET_COPY_PERSISTED_PAYLOAD_CHANGED'):
                adapters._tiktok_publish_price(changed,site)
        with pytest.raises(RuntimeError,match='NATIVE_TARGET_COPY_EXACT_TARGET_REQUIRED'):
            adapters._tiktok_publish_price(payload,'HB_PH')
        consumed.append(label)
        return '7:8',{'source':'miaoshou_open_api','accepted':True,'detail_id':7,'shop_id':8,
            'write_outcome':'submission_accepted','external_writes_performed':['owned:submission']}
    monkeypatch.setattr(adapters,'_miaoshou_publish_target',accepted)
    def dispatch(request):
        if request.target_label in {'tiktok:LH_MY','tiktok:LH_PH'}:
            return adapters.execute_tiktok_target(request)
        return AdapterExecutionResult(False,False,'Owned unrelated target remains closed',None,
            {'source':'OWNED_UNIT_NO_PROVIDER','external_writes_performed':[]})
    monkeypatch.setattr(adapters,'production_adapter_registry',lambda:{name:replace(item,
        execute=dispatch,blocker=None,automatic_first_attempt_mode='ENABLED')
        for name,item in original.items()})
    try:
        review=installed.prepare(market['plan_id'])
        code,result=installed.approve_and_submit(nonce=review['nonce'],review_digest=review['review_digest'])
        assert code==202,result
        _join(installed)
        assert sorted(consumed)==['tiktok:LH_MY','tiktok:LH_PH']
        assert installed.store.get_plan(market['plan_id'])['payload']==market['payload']
        run=installed.store.get_run('release-run:'+market['payload_digest'][:24])
        rows=[row for row in run['targets'] if row['target_label'] in consumed]
        assert len(rows)==2 and all(row['attempts']==1 and row['status']=='SUBMITTED_UNVERIFIED'
                                   and not row['readback'] for row in rows)
    finally:installed.close()


def test_nonuniform_sku_publication_prices_remain_unsupported_without_collapsing_or_discount_defaults():
    # This pure capability check grants no native context or transport. Its
    # actual service caller separately re-reads the decision/full source graph.
    frozen={'price':{'sku_prices':[{'model_sku':'first','amount':25,'currency':'MYR'},
                                 {'model_sku':'second','amount':30,'currency':'MYR'}]}}
    reviewed=[{'seller_sku':'first'},{'seller_sku':'second'}]
    projection=[{'model_sku':'first','list_price':25,'currency':'MYR'},
                {'model_sku':'second','list_price':30,'currency':'MYR'}]
    before=deepcopy((frozen,reviewed,projection))
    with pytest.raises(RuntimeError,match='NATIVE_TARGET_PRICE_PER_SKU_TRANSPORT_REQUIRED'):
        adapters._single_native_publish_price(frozen,reviewed,projection,'MYR')
    assert (frozen,reviewed,projection)==before
    for damage,code in [('missing','EXACT_SKUS_REQUIRED'),('duplicate','EXACT_SKUS_REQUIRED'),
                        ('foreign-currency','FROZEN_VALUE_INVALID'),('nan','FROZEN_VALUE_INVALID'),
                        ('changed-projection','PROJECTION_CHANGED')]:
        changed=deepcopy(frozen);shown=deepcopy(projection)
        if damage=='missing':changed['price']['sku_prices'].pop()
        elif damage=='duplicate':changed['price']['sku_prices'][1]['model_sku']='first'
        elif damage=='foreign-currency':changed['price']['sku_prices'][1]['currency']='USD'
        elif damage=='nan':changed['price']['sku_prices'][1]['amount']='NaN'
        else:shown[1]['list_price']=31
        with pytest.raises(RuntimeError,match='NATIVE_TARGET_PRICE_'+code):
            adapters._single_native_publish_price(changed,reviewed,shown,'MYR')


def test_legacy_publication_price_still_requires_original_store_price_contract():
    payload={'pricing':{'selected_targets':{'tiktok:LH_MY':{
        'sku_prices':[{'model_sku':'one','list_price':25,'currency':'MYR'}]}}}}
    with pytest.raises(RuntimeError,match='requires exactly one approved store price'):
        adapters._tiktok_publish_price(payload,'LH_MY')
    row={'list_price':25,'currency':'MYR','discount_reserve_pct':35,'sale_after_discount':16.25}
    payload['pricing']['selected_targets']['tiktok:LH_MY']['store_prices']=[row]
    assert adapters._tiktok_publish_price(payload,'LH_MY')==row
