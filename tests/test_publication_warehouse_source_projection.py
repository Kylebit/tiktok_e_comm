"""Typed source allocations survive R1/COMMON projection/real final snapshot."""
from copy import deepcopy
import pytest
from test_publication_gallery_preparation_metadata import source
from owned_new_publication_task import owned_warehouse_allocations,complete_new_task
from test_round1_workspace_freeze import live
from test_round1_auto_freeze import public_settings


def preview():
    labels=['tiktok:LH_PH','tiktok:HB_PH','shopee:PH','ozon:RU']
    return {'product':{'title':'Owned fixture','source_image_count':0,
        'warehouse_inventory_by_target':owned_warehouse_allocations(labels[:2])},
        'publication_scope':{'selected_labels':labels}}


def test_real_r1_keeps_full_typed_allocation_without_mutating_source_or_inventing_zeros():
    value=preview();original=deepcopy(value)
    result=source()._safe_product_facts(value,value['publication_scope']['selected_labels'])
    assert result['warehouse_inventory_by_target']==value['product']['warehouse_inventory_by_target']
    assert value==original
    for row in result['warehouse_inventory_by_target'].values():
        assert [w['stock'] for w in row['warehouses']]==[150,50]
    value['product'].pop('warehouse_inventory_by_target')
    assert 'warehouse_inventory_by_target' not in source()._safe_product_facts(value)


@pytest.mark.parametrize('damage',['missing-shop','extra-shop','duplicate-shop','wrong-total','boolean','guessed-source'])
def test_real_r1_rejects_incomplete_or_untyped_warehouse_scope(damage):
    value=preview();allocation=value['product']['warehouse_inventory_by_target'];row=allocation['tiktok:LH_PH']
    if damage=='missing-shop':allocation.pop('tiktok:HB_PH')
    elif damage=='extra-shop':allocation['tiktok:LH_MY']=deepcopy(row)
    elif damage=='duplicate-shop':allocation['tiktok:HB_PH']['shop_id']=row['shop_id']
    elif damage=='wrong-total':row['total_stock']=201
    elif damage=='boolean':row['warehouses'][0]['stock']=True
    else:row['source']='AGENT_ASSUMED_OTHER_WAREHOUSES_ZERO'
    module=source()
    with pytest.raises(module.PreparationError,match='R1_WAREHOUSE_ALLOCATION_INVALID'):
        module._safe_product_facts(value,value['publication_scope']['selected_labels'])


def test_real_new_task_freezes_original_scope_then_projects_only_marketplace_inventory(live,monkeypatch,tmp_path):
    from shared_platform.publication_r3_image_bridge import load_r2_documents,prepare_common_stage_dashboard
    from shared_platform import release_control
    from domains.product_operations.approved_publication_snapshot import build_publication_preview
    runtime,market,task,calls=complete_new_task(live,monkeypatch,tmp_path)
    try:
        documents=load_r2_documents(live['offer'])
        original=documents['first_review']['product_facts']['warehouse_inventory_by_target']
        assert documents['round1_snapshot']['fact_snapshot']['product_facts']['warehouse_inventory_by_target']==original
        assert market['payload']['product_facts']['warehouse_inventory_by_target']==original
        assert build_publication_preview(market['payload'])['product']['warehouse_inventory_by_target']==original
        assert all(w['stock']>0 for row in original.values() for w in row['warehouses'])
        dashboard=release_control.build_release_dashboard(offer_id=live['offer'])
        common=prepare_common_stage_dashboard(dashboard,documents)
        assert 'warehouse_inventory_by_target' not in common['product']
        dashboard['product']['warehouse_inventory_by_target'][next(iter(original))]['total_stock']=999
        with pytest.raises(ValueError):prepare_common_stage_dashboard(dashboard,documents)
    finally:runtime.close()


def test_real_source_projection_does_not_choose_between_conflicting_source_and_review():
    from shared_platform.release_control import _source_review_warehouse_facts
    allocation=owned_warehouse_allocations(['tiktok:LH_PH','tiktok:HB_PH'])
    state={'review':{'selected_sites':['lh_ph','hb_ph'],'warehouse_inventory_by_target':allocation},'source':{}}
    assert _source_review_warehouse_facts(state)==allocation
    state['source']['warehouse_inventory_by_target']=deepcopy(allocation)
    assert _source_review_warehouse_facts(state)==allocation
    state['source']['warehouse_inventory_by_target']['tiktok:LH_PH']['warehouses'][0]['stock']=149
    state['source']['warehouse_inventory_by_target']['tiktok:LH_PH']['warehouses'][1]['stock']=51
    with pytest.raises(ValueError,match='WAREHOUSE_SOURCE_REVIEW_CONFLICT'):_source_review_warehouse_facts(state)


def test_save_review_cannot_change_frozen_warehouse_allocation_or_save_invalid_types(monkeypatch):
    from modules.sourcing import new_product_workbench as workbench
    allocation=owned_warehouse_allocations(['tiktok:LH_PH'])
    state={'review':{'selected_sites':['lh_ph'],'warehouse_inventory_by_target':allocation,'fields_locked':True},
           'product_approval':{'status':'approved'},'source':{}}
    monkeypatch.setattr(workbench,'resolve_offer_key',lambda offer:offer)
    monkeypatch.setattr(workbench,'load_state',lambda offer:deepcopy(state))
    monkeypatch.setattr(workbench,'save_state',lambda *a,**k:pytest.fail('rejected facts must not persist'))
    changed=deepcopy(allocation);changed['tiktok:LH_PH']['total_stock']=201
    with pytest.raises(ValueError,match='approved product facts are locked'):
        workbench.save_review('3828811808',{'warehouse_inventory_by_target':changed})
    state['review']['fields_locked']=False;state['product_approval']={}
    changed['tiktok:LH_PH']['warehouses'][0]['stock']=True
    with pytest.raises(ValueError,match='warehouse stock'):
        workbench.save_review('3828811808',{'warehouse_inventory_by_target':changed})
