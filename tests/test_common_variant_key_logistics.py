from copy import deepcopy
import pytest
from modules.products import server, release_adapters as adapters
from test_b4b_common_stage import context, CommonTransport

KEY=';th3655-58*22cm*4pcs;;'


def replace(value):
    if isinstance(value,dict):return {KEY if key=='default' else key:replace(item) for key,item in value.items()}
    if isinstance(value,list):return [replace(item) for item in value]
    return KEY if value=='default' else value


def case(tmp_path,monkeypatch):
    _,_,_,request=context(tmp_path,monkeypatch)
    code,view=server._preview_r3_common_stage(request)
    assert code==200
    payload=replace(view['common']['plan']['payload'])
    transport=CommonTransport(monkeypatch)
    transport.detail=replace(transport.detail)
    return payload,transport


def test_semicolon_provider_key_full_write_and_readback_keeps_original_key(tmp_path,monkeypatch):
    payload,transport=case(tmp_path,monkeypatch)
    before=deepcopy(payload)
    result=adapters.write_miaoshou_common_from_plan(payload,post=transport.post)
    assert result['verified'] and all(result['checks'].values())
    assert transport.mutations==1 and transport.reads==2
    assert set(transport.detail['skuMap'])=={KEY}
    assert payload==before


@pytest.mark.parametrize('mode',['collision','missing'])
def test_ambiguous_or_missing_logistics_key_never_edits(tmp_path,monkeypatch,mode):
    payload,transport=case(tmp_path,monkeypatch)
    facts=payload['product_facts']['sku_commercial_facts']
    if mode=='collision':facts[KEY.strip(';')]=deepcopy(facts[KEY])
    else:facts.clear()
    with pytest.raises(ValueError,match='COMMON_FROZEN_SKU_LOGISTICS_KEY_CONFLICT'):
        adapters.write_miaoshou_common_from_plan(payload,post=transport.post)
    assert transport.mutations==0


def test_third_attempt_cannot_skip_unresolved_second_guard(tmp_path,monkeypatch):
    from shared_platform import operations_domain_guard as guard
    from shared_platform.workbench_engine import WorkbenchEngine
    from shared_platform import release_store
    from types import SimpleNamespace
    engine=WorkbenchEngine(tmp_path/'tasks.db',{'code_version':'test'})
    monkeypatch.setattr(guard,'engine_for',lambda root:engine)
    plan={'plan_id':'test-plan','payload_digest':'a'*64,'product_id':'1','targets':['miaoshou:COMMON'],
          'payload':{'seller_sku':'0988','r3_stage_binding':{'marketplace_targets':['shopee:MY']}}}
    operation='publication-common:test-plan'
    engine.begin_domain_operation(operation,skus=['0988'],shops=['shopee:MY'])
    # Synthetic local close, deliberately keep attempt 2 in flight.
    with engine.transaction() as db:
        db.execute("UPDATE workbench_domain_operations SET state='completed',readback_ref='local-not-dispatched:fixture'")
        db.execute('DELETE FROM workbench_domain_locks')
    engine.begin_domain_operation(operation+':attempt:2',skus=['0988'],shops=['shopee:MY'])
    monkeypatch.setattr(release_store,'default_release_store',lambda:SimpleNamespace(get_run=lambda run:{'targets':[{'target_label':'miaoshou:COMMON','attempts':3}]}))
    with pytest.raises(ValueError,match='all prior attempts'):
        guard.begin_common(plan,tmp_path)
