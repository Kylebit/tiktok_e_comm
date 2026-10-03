"""Native approved graph consumers, with every provider endpoint closed."""
from copy import deepcopy
from dataclasses import replace

import pytest

from modules.products import release_adapters as adapters
from domains.channel_operations.release_executor import AdapterExecutionResult
from test_round1_workspace_freeze import live
from test_native_sole_final_service import _installed
from test_native_sole_final_continuation import _join


def test_legacy_country_candidate_contract_and_caller_native_dict_do_not_gain_a_decision():
    payload={'listing_copy':{'candidates':[{'channel':'tiktok','site':'MY',
               'policy_check':'passed','title':'Original legacy title'}]}}
    assert adapters._candidate(payload,'tiktok','MY')=='Original legacy title'
    assert adapters._tiktok_target_title(payload,'LH_MY')=='Original legacy title'
    with pytest.raises(RuntimeError,match='NATIVE_TARGET_COPY_DECISION_REQUIRED'):
        adapters._native_frozen_target_title(payload,channel='tiktok',site='LH_MY',
                                              target_label='tiktok:LH_MY')
    with pytest.raises(RuntimeError,match='approved listing title candidate is missing'):
        adapters._candidate({'r3_marketplace_binding':{'route':{'title':'caller proof'}}},'ozon','RU')


def test_real_decision_target_claim_rechecks_shared_frozen_copy_and_rejects_other_scope(live,monkeypatch,tmp_path):
    installed,market,*_=_installed(live,monkeypatch,tmp_path)
    original=adapters.production_adapter_registry()
    consumed=[];rejected=[]
    # No legacy title rows are manufactured in this actual immutable graph.
    assert market['payload']['listing_copy']['candidates']==[]
    with pytest.raises(RuntimeError,match='approved listing title candidate is missing'):
        adapters._candidate(market['payload'],'shopee','CNSC')
    def no_provider(*args,**kwargs):
        pytest.fail('Only the owned exact submission seam is permitted')
    monkeypatch.setattr(adapters,'_tiktok_readback',no_provider)
    monkeypatch.setattr(adapters.auth,'access_token',no_provider)
    monkeypatch.setattr(adapters,'_repair_tiktok_title',no_provider)
    monkeypatch.setattr(adapters,'_cache_verified_tiktok_listing',no_provider)
    def accepted(payload,*,site):
        label='tiktok:'+site
        title=payload['r3_marketplace_binding']['route']['target_facts'][label]['copy']['title']
        assert adapters._tiktok_target_title(payload,site)==title
        assert adapters._immutable_miaoshou_plan_draft(payload,site=site)['title']==title
        if site=='LH_PH':
            for damage in ('copy','route','round1','plan'):
                changed=deepcopy(payload)
                if damage=='copy':changed['product_facts']['content_by_target'][label]['title']='Foreign country title'
                elif damage=='route':changed['r3_marketplace_binding']['route']['target_facts'][label]['copy']['title']='Another brand'
                elif damage=='round1':changed['r3_marketplace_binding']['documents']['round1_snapshot']['snapshot_digest']='sha256:'+'0'*64
                else:changed['plan_id']='caller-plan'
                with pytest.raises(RuntimeError,match='NATIVE_TARGET_COPY_PERSISTED_PAYLOAD_CHANGED'):
                    adapters._tiktok_target_title(changed,site)
                rejected.append(damage)
            for wrong in ('LH_TH','HB_PH'):
                with pytest.raises(RuntimeError,match='NATIVE_TARGET_COPY_EXACT_TARGET_REQUIRED'):
                    adapters._tiktok_target_title(payload,wrong)
                rejected.append(wrong)
            with pytest.raises(RuntimeError,match='NATIVE_TARGET_COPY_EXACT_TARGET_REQUIRED'):
                adapters._native_frozen_target_title(payload,channel='shopee',site='PH',target_label=label)
            rejected.append('cross-platform')
            shopee=payload['r3_marketplace_binding']['route']['target_facts']['shopee:PH']['copy']
            assert shopee['language']=='en'
            assert adapters._candidate(payload,'shopee','CNSC')==shopee['title']
            assert adapters._candidate(payload,'ozon','RU')==payload['r3_marketplace_binding']['route']['target_facts']['ozon:RU']['copy']['title']
            with pytest.raises(RuntimeError,match='NATIVE_TARGET_COPY_EXACT_TARGET_REQUIRED'):
                adapters._candidate(payload,'shopee','GB')
            rejected.append('wrong-shopee-country')
        consumed.append(label)
        return '7:8',{'source':'miaoshou_open_api','accepted':True,'detail_id':7,'shop_id':8,
                     'write_outcome':'submission_accepted','external_writes_performed':['owned:submission']}
    monkeypatch.setattr(adapters,'_miaoshou_publish_target',accepted)
    def dispatch(request):
        if request.target_label in {'tiktok:LH_PH','tiktok:LH_MY'}:
            return adapters.execute_tiktok_target(request)
        return AdapterExecutionResult(False,False,'Owned unrelated target remains unexecuted',None,
            {'source':'OWNED_UNIT_NO_PROVIDER','verified':False,'target_label':request.target_label,
             'external_writes_performed':[]})
    monkeypatch.setattr(adapters,'production_adapter_registry',lambda:{name:replace(item,
        execute=dispatch,blocker=None,automatic_first_attempt_mode='ENABLED')
        for name,item in original.items()})
    try:
        prepared=installed.prepare(market['plan_id'])
        code,result=installed.approve_and_submit(nonce=prepared['nonce'],review_digest=prepared['review_digest'])
        assert code==202,result
        _join(installed)
        assert sorted(consumed)==['tiktok:LH_MY','tiktok:LH_PH']
        assert set(rejected)=={'copy','route','round1','plan','LH_TH','HB_PH','cross-platform','wrong-shopee-country'}
        run=installed.store.get_run('release-run:'+market['payload_digest'][:24])
        native=[row for row in run['targets'] if row['target_label'] in consumed]
        assert all(row['attempts']==1 and row['status']=='SUBMITTED_UNVERIFIED' and not row['readback'] for row in native)
        assert installed.store.get_plan(market['plan_id'])['payload']==market['payload']
    finally:installed.close()


def test_real_shopee_and_ozon_consumers_keep_missing_description_and_inventory_closed(live,monkeypatch,tmp_path):
    installed,market,*_=_installed(live,monkeypatch,tmp_path)
    original=adapters.production_adapter_registry()
    seen=[]
    def no_provider(*args,**kwargs):
        pytest.fail('A frozen title does not authorize provider READ or WRITE')
    monkeypatch.setattr(adapters,'_miaoshou_publish_target',no_provider)
    monkeypatch.setattr(adapters,'_shopee_readback',no_provider)
    monkeypatch.setattr(adapters,'_ozon_readback',no_provider)
    monkeypatch.setattr(adapters.auth,'access_token',no_provider)
    def dispatch(request):
        payload=installed.store.get_plan(request.plan_id)['payload']
        if request.target_label=='shopee:PH':
            assert adapters._candidate(payload,'shopee','CNSC')==payload['r3_marketplace_binding']['route']['target_facts']['shopee:PH']['copy']['title']
            # The original frozen fixture has 49 description characters. Keep
            # this real remaining capability guard, rather than fake 500 chars.
            with pytest.raises(RuntimeError,match='approved Shopee global description is too short'):
                adapters.execute_shopee_target(request)
            seen.append('shopee:PH')
        elif request.target_label=='ozon:RU':
            assert adapters._candidate(payload,'ozon','RU')==payload['r3_marketplace_binding']['route']['target_facts']['ozon:RU']['copy']['title']
            # A warehouse observation is not an immutable v2 stock command.
            with pytest.raises(RuntimeError,match='Ozon automatic release requires an immutable Kyle-approved inventory decision'):
                adapters.execute_ozon_target(request)
            seen.append('ozon:RU')
        return AdapterExecutionResult(False,False,'Original missing capability remains blocked',None,
            {'source':'OWNED_CAPABILITY_NEGATIVE','verified':False,'target_label':request.target_label,
             'external_writes_performed':[]})
    monkeypatch.setattr(adapters,'production_adapter_registry',lambda:{name:replace(item,
        execute=dispatch,blocker=None,automatic_first_attempt_mode='ENABLED')
        for name,item in original.items()})
    try:
        prepared=installed.prepare(market['plan_id'])
        code,result=installed.approve_and_submit(nonce=prepared['nonce'],review_digest=prepared['review_digest'])
        assert code==202,result
        _join(installed)
        assert sorted(seen)==['ozon:RU','shopee:PH']
        run=installed.store.get_run('release-run:'+market['payload_digest'][:24])
        assert all(row['attempts']==1 and row['status']=='FAILED' and not row['readback']
                   and not row['submission'] for row in run['targets'])
        assert installed.store.get_plan(market['plan_id'])['payload']==market['payload']
    finally:installed.close()
