"""R1 keeps truthful role evidence which the actual R3 gallery gate consumes."""
from copy import deepcopy
from importlib.util import spec_from_file_location,module_from_spec
from pathlib import Path
import pytest
from test_native_parent_images import _image_plan
from shared_platform.publication_gallery_contract import validate_gallery_plan

def source():
    path=Path(__file__).resolve().parents[1]/'skills/prepare-product-publication/scripts/prepare_product_publication.py'
    spec=spec_from_file_location('ps_r1_image_metadata',path);m=module_from_spec(spec);spec.loader.exec_module(m);return m

def truthful_plan():
    plan=_image_plan()
    for brand in plan['brand_plans']:
        brand['generated_assets']=[{'role':r,'quantity':1,'brief':'Visible actual product '+r}
            for r in ('cover','product_detail','scene_1','scene_2','scene_3','size_comparison','installation')]
        for asset in brand['generated_assets']:
            if asset['role'] in {'size_comparison','installation'}:
                old=asset['role'];asset['role']='proportion_evidence' if old=='size_comparison' else 'construction_evidence'
                asset['role_substitution']={'replaces':old,'reason':'SOURCE_FACT_UNKNOWN',
                    'evidence_basis':'Visible product construction only; no measurements or mounting claims.',
                    'unknown_facts_not_asserted':True}
    return plan

def test_truthful_unknown_fact_substitutions_survive_real_r1_and_r3():
    module=source();plan=truthful_plan();before=deepcopy(plan)
    normalized=module._safe_image_execution_plan(plan)
    assert plan==before
    for original,retained in zip(plan['brand_plans'],normalized['brand_plans']):
        assert original['generated_assets']==retained['generated_assets']
    round1={'image_plan':normalized,'canonical_targets':['tiktok:LH_PH','tiktok:HB_PH'],
        'fact_snapshot':{'product_facts':{'title':'Household fabric organiser','skus':[]}}}
    assert set(validate_gallery_plan(round1)['roles_by_brand'])=={'livelyhive-sea','homebloom-sea'}

def test_family_specific_unknown_size_and_layout_do_not_force_fabricated_dimensions():
    module=source();plan=truthful_plan()
    roles=['cover_scene','installed_detail','scene_1','scene_2','scene_3','proportion_evidence','construction_evidence']
    for brand in plan['brand_plans']:
        for asset,role in zip(brand['generated_assets'],roles):
            asset['role']=role
            if 'role_substitution' in asset:
                asset['role_substitution']['replaces']='assembled_size' if role=='proportion_evidence' else 'piece_layout_or_instructions'
    normalized=module._safe_image_execution_plan(plan)
    result=validate_gallery_plan({'image_plan':normalized,'canonical_targets':['tiktok:LH_PH','tiktok:HB_PH'],
        'fact_snapshot':{'product_facts':{'title':'Dog Wall Decal','category_semantic':'wall_sticker','skus':[]}}})
    assert result['minimum_per_brand']==7
    assert result['roles_by_brand']['livelyhive-sea']==roles

@pytest.mark.parametrize('damage',['false_truth','fake_reason','unknown_field','both_claim_and_substitution'])
def test_bad_metadata_does_not_borrow_truthful_substitution(damage):
    module=source();plan=truthful_plan()
    asset=next(a for a in plan['brand_plans'][0]['generated_assets'] if 'role_substitution' in a)
    if damage=='false_truth':asset['role_substitution']['unknown_facts_not_asserted']=False
    elif damage=='fake_reason':asset['role_substitution']['reason']='AGENT_GUESSED_MEASUREMENTS'
    elif damage=='unknown_field':asset['unreviewed_write_authority']=True
    else:asset['fact_evidence']={'fact_path':['product_facts'],'value':{'fact_verified':True,'status':'VERIFIED'}}
    with pytest.raises(module.PreparationError):module._safe_image_execution_plan(plan)
