"""Real new-task R1/R2/COMMON/actor graph; only owned external edges closed.

No old B/403 adoption, retained provider database or fabricated approval row.
"""
from copy import deepcopy
import json
from pathlib import Path
from shared_platform.workbench_engine import WorkbenchEngine


def complete_new_task(live,monkeypatch,tmp_path):
    import test_common_native_preparation_source as preparation
    import test_round1_sidecar_convergence as sidecar
    from test_native_sole_final_service import _installed
    from shared_platform import release_control
    from shared_platform.native_task_preparation import ExplicitNewTaskWorker,NativeR1FilesystemBoundary
    original_source=preparation.complete_source
    def source(value):
        original_source(value)
        path=value['root']/'data/new_product_workbench'/f"{value['offer']}.json"
        state=json.loads(path.read_bytes())
        state['review']['selected_sku_keys']=['size-large','size-medium','size-small']
        state['source']['skus']=[{'key':key,'name':name,'price':4.4,'image':f'https://example.com/variant-{index}.png'}
            for index,(key,name) in enumerate(zip(state['review']['selected_sku_keys'],('Large','Medium','Small')))]
        from shared_platform.publication_rounds import CANONICAL_TIKTOK_TO_WORKBENCH
        labels=[label for label,site in CANONICAL_TIKTOK_TO_WORKBENCH.items()
            if site in state['review']['selected_sites']]
        # Original persisted source, before the actual capture/R1 producer.
        # No dashboard, frozen packet, or downstream consumer is replaced.
        state['review']['warehouse_inventory_by_target']=owned_warehouse_allocations(labels)
        path.write_text(json.dumps(state),encoding='utf-8')
    monkeypatch.setattr(preparation,'complete_source',source)
    original_images=preparation._image_proposal
    def image_proposal():
        plan=original_images()
        for brand in plan['brand_plans']:
            for asset,key,name in zip(brand['generated_assets'],('size-large','size-medium','size-small'),('Large','Medium','Small')):
                asset['variant_keys']=[key]
                asset['brief']='Faithful supplied '+name+' option only; no inferred numerical dimensions.'
        return plan
    monkeypatch.setattr(preparation,'_image_proposal',image_proposal)
    original_create=WorkbenchEngine.create
    def explicit_create(engine,payload):
        if payload.get('source_key','').startswith('native-source-'):
            return engine.create_for_explicit_post(payload)[0]
        return original_create(engine,payload)
    monkeypatch.setattr(WorkbenchEngine,'create',explicit_create)
    original_candidate=sidecar.candidate
    def candidate(target):
        result=original_candidate(target)
        dashboard=release_control.build_release_dashboard(offer_id=live['offer'])
        seller=dashboard['product']['seller_sku_candidate']
        result['target_candidates'][0]['copy']['variants']=[{'seller_sku':str(int(seller)+i).zfill(4),'display_name':name}
            for i,name in enumerate(('Large','Medium','Small'))]
        return result
    monkeypatch.setattr(sidecar,'candidate',candidate)
    installed,market,common,common_run,calls=_installed(live,monkeypatch,tmp_path)
    operations=installed._operations
    worker=ExplicitNewTaskWorker(operations.engine,operations.profile,NativeR1FilesystemBoundary(operations.profile))
    operations.new_task_worker=worker
    assert len(operations.engine.explicit_new_post_task_ids())==1
    task=operations.engine.get(operations.engine.explicit_new_post_task_ids()[0])
    assert worker.owns(task['task_id']) and len(market['payload']['product_facts']['selected_sku_keys'])==3
    from shared_platform import native_final_mutation_budget as budget
    from shared_platform import oneclick_release_controlplane as oneclick
    with installed.store._connect() as db:
        db.executescript(budget.SCHEMA_SQL)
        db.executescript(oneclick._SCHEMA)
    catalog=live['root']/'data/shop.db'
    monkeypatch.setenv('ORBIT_CATALOG_DATABASE',str(catalog))
    original_close=installed.close
    def close():
        try:return original_close()
        finally:worker.close()
    monkeypatch.setattr(installed,'close',close)
    return installed,market,task,calls


def owned_warehouse_allocations(labels):
    return {label:{'schema_version':'miaoshou-tiktok-warehouse-allocation/v1',
        'shop_id':str(91000+index),
        'warehouses':[{'warehouse_id':str(92000+index*2),'warehouse_name':'Owned mainland fixture','stock':150},
            {'warehouse_id':str(92001+index*2),'warehouse_name':'Owned regional fixture','stock':50}],
        'total_stock':200,'source':'CONVERSATION_APPROVAL',
        'approved_by':'OWNED_OFFLINE_FIXTURE_NOT_LIVE_AUTHORITY','approved_at':'2026-10-31T00:00:00+00:00'}
        for index,label in enumerate(sorted(labels))}
