"""Reusable publication input/compiler boundary; external edges stay closed."""
from copy import deepcopy
from pathlib import Path
import hashlib,json
import pytest

from shared_platform.publication_execution_package import (
    ExecutionPackageError, compile_publication_execution_package,
    validate_publication_execution_package, render_package_review,
)

def test_missing_material_lists_all_dependency_gaps_before_review(tmp_path):
    value=compile_publication_execution_package({},task_id='new-task',
        source_identity={'root':str(tmp_path),'head':'1'*40,'manifest_digest':'2'*64},
        runtime={'base_url':'http://127.0.0.1:49999','expected_source_root':str(tmp_path),'expected_source_head':'1'*40})
    assert value['status']=='BLOCKED'
    assert value['execution_authorized'] is False
    assert {'PUBLICATION_PAYLOAD_MISSING','R2_IMAGE_ARTIFACTS_MISSING','COMMON_READBACK_MISSING',
            'CAPABILITY_OBSERVATIONS_MISSING'} <= {b['code'] for b in value['blockers']}
    with pytest.raises(ExecutionPackageError):
        validate_publication_execution_package(value)

def test_package_digest_and_exact_scope_cannot_be_replaced(tmp_path):
    value=compile_publication_execution_package({},task_id='new-task',
        source_identity={'root':str(tmp_path),'head':'1'*40,'manifest_digest':'2'*64},
        runtime={'base_url':'http://127.0.0.1:49999','expected_source_root':str(tmp_path),'expected_source_head':'1'*40})
    value['offer_id']='8888888888'
    with pytest.raises(ExecutionPackageError,match='DIGEST'):
        validate_publication_execution_package(value)

def three_model_material(tmp_path):
    """Inert compiler fixture, not an approval or claimed native end-to-end run."""
    from test_b4b_release_compiler import neutral_product_plan,policy,INCIDENTS
    from shared_platform.native_common_budget_facts import _READBACK_CHECKS
    from PIL import Image
    payload=deepcopy(neutral_product_plan()['payload'])
    payload.update(product_id='9012345678',plan_id='release-plan:9012345678:new-three')
    facts=payload['product_facts'];facts['selected_sku_keys'].append('green-38x45')
    facts['sku_commercial_facts']['green-38x45']=deepcopy(facts['sku_commercial_facts']['pink-38x45'])
    facts['sku_commercial_facts']['green-38x45']['specification']={'color':'Green','size':'38x45cm'}
    payload['sku_lineage']['assignment']['model_skus'].append({'variant_key':'green-38x45','model_sku':'0960'})
    master=payload['shopee_global_master']
    master['sku_original_prices_cny'].append({'model_sku':'0960','amount':'41.25','currency':'CNY'})
    for price in payload['pricing']['selected_targets'].values():
        price['sku_prices'].append({**price['sku_prices'][-1],'model_sku':'0960'})
    assets=[];urls=[]
    for i,role in enumerate(('cover','product_detail','scene_1','scene_2','scene_3','proportion_evidence','construction_evidence')):
        path=tmp_path/('asset-'+str(i)+'.png');Image.new('RGB',(32,32),(i*30,128,250)).save(path)
        url='https://img.example/new-9012-'+str(i)+'.png';urls.append(url)
        assets.append({'url':url,'path':str(path),'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'role':role,'brand':'livelyhive-sea'})
    facts['image_urls']=urls
    for i,key in enumerate(facts['selected_sku_keys']):facts['sku_commercial_facts'][key]['image_urls']=[urls[i]]
    master['variant_image_positions']=[{'model_sku':model,'position':i,'image_url':urls[i]} for i,model in enumerate(('0958','0959','0960'))]
    capabilities={label:{**{name:True for name in ('category_attributes','variant_images','price_precision','publication_readback','inventory_readback','existing_auth')},
                         'evidence_digest':'3'*64} for label in payload['targets']}
    return {'immutable_plan_payload':payload,'policy':policy(40),'incident_registry':INCIDENTS,'image_assets':assets,
        'common_readback':{'verified':True,'offer_id':'9012345678','checks':{name:True for name in _READBACK_CHECKS}},
        'capability_observations':capabilities}

def test_actual_preview_compiler_demands_durable_evidence_for_new_three_models(tmp_path):
    inputs=three_model_material(tmp_path)
    value=compile_publication_execution_package(inputs,task_id='original-new-task',
        source_identity={'root':str(tmp_path),'head':'1'*40,'manifest_digest':'2'*64},
        runtime={'base_url':'http://127.0.0.1:49999','expected_source_root':str(tmp_path),'expected_source_head':'1'*40})
    assert value['status']=='BLOCKED'
    assert {b['code'] for b in value['blockers']}=={'DURABLE_EVIDENCE_REQUIRED'}
    assert [s['model_sku'] for s in value['snapshot']['skus']]==['0958','0959','0960']
    with pytest.raises(ExecutionPackageError,match='PACKAGE_NOT_READY'):validate_publication_execution_package(value)
    rendered=render_package_review(value)
    assert 'new-9012-6.png' in rendered and all(s in rendered for s in ('0958','0959','0960'))
    assert value['execution_authorized'] is False

def test_changed_real_artifact_and_missing_target_capabilities_are_collected_together(tmp_path):
    inputs=three_model_material(tmp_path)
    Path(inputs['image_assets'][0]['path']).write_bytes(b'changed')
    del inputs['capability_observations']['ozon:RU']
    value=compile_publication_execution_package(inputs,task_id='original-new-task',
        source_identity={'root':str(tmp_path),'head':'1'*40,'manifest_digest':'2'*64},
        runtime={'base_url':'http://127.0.0.1:49999','expected_source_root':str(tmp_path),'expected_source_head':'1'*40})
    assert {'IMAGE_BYTES_CHANGED','TARGET_CAPABILITY_OBSERVATION_MISSING'}<={b['code'] for b in value['blockers']}
