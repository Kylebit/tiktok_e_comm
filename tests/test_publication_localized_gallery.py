"""Actual R2 bridge's qualified locale routes survive the final gallery gate."""
from copy import deepcopy
import hashlib
import pytest
from test_publication_gallery_preparation_metadata import truthful_plan,source
from shared_platform.publication_r3_image_bridge import build_dual_brand_publication_bridge
from shared_platform.publication_gallery_contract import validate_gallery_documents


def localized_documents():
    plan=source()._safe_image_execution_plan(truthful_plan())
    labels=['tiktok:LH_PH','tiktok:LH_MY','tiktok:HB_PH']
    first={'offer_id':'9012345678','status':'FIRST_REVIEW_READY','image_execution_plan':plan,
        'target_selection':{'requested':labels},'targets':[{'target':t,'category':{},'copy':{},'price':{}} for t in labels]}
    assets=[]
    for brand in plan['brand_plans']:
        for asset in brand['generated_assets']:
            number=len(assets)+1
            assets.append({'brand_id':brand['id'],'role':asset['role'],'status':'COMPLETED','review_number':number,
                'public_url':f'https://images.example/master-{number}.png','artifact_id':f'master-{number}',
                'artifact_digest':'sha256:'+hashlib.sha256(str(number).encode()).hexdigest(),'task_id':number})
    tasks=[];localized=[]
    for master in assets[:2]:
        tasks.append({'brand_id':master['brand_id'],'role':master['role'],'locale':'ms-MY',
            'review_number':master['review_number'],'source_url':master['public_url'],'source_artifact_digest':master['artifact_digest']})
        localized.append({'brand_id':master['brand_id'],'role':master['role'],'locale':'ms-MY',
            'source_review_number':master['review_number'],'status':'COMPLETED',
            'public_url':f"https://images.example/ms-{master['review_number']}.png",
            'artifact_digest':'sha256:'+hashlib.sha256(('ms'+str(master['review_number'])).encode()).hexdigest()})
    translated={'offer_id':first['offer_id'],'status':'LOCALIZED_IMAGE_REVIEW_REQUIRED','approved_task_count':2,'approved_tasks':tasks,'assets':localized}
    generation={'offer_id':first['offer_id'],'status':'BRAND_IMAGE_REVIEW_REQUIRED','assets':assets}
    docs={'first_review':first,'round1_snapshot':{'image_plan':plan,'canonical_targets':labels,
        'fact_snapshot':{'product_facts':{'title':'Household organiser','skus':[]}}},
        'generation_result':generation,'translation_plan':{'tasks':tasks},'translation_result':translated,
        'image_qa':{'status':'PASSED','artifact_digests':[r['artifact_digest'] for r in assets+localized]}}
    route=build_dual_brand_publication_bridge(offer_id=first['offer_id'],first_review=first,generation=generation,translation=translated,
        approved_by='product-publication-autopilot')
    return docs,route


def test_real_locale_route_is_not_mistaken_for_missing_master_gallery():
    docs,route=localized_documents()
    assert route['image_routes']['tiktok:LH_MY'][0]['kind']=='LOCALIZED_ARTIFACT'
    result=validate_gallery_documents(docs,route)
    assert result['status']=='PASSED' and result['total_distinct_qualified']==14

@pytest.mark.parametrize('damage',['wrong-source','unqualified','wrong-route'])
def test_locale_route_still_requires_exact_source_master_locale_and_qa(damage):
    docs,route=localized_documents()
    if damage=='wrong-source':docs['translation_plan']['tasks'][0]['source_artifact_digest']='sha256:'+'0'*64
    elif damage=='unqualified':docs['image_qa']['artifact_digests'].remove(docs['translation_result']['assets'][0]['artifact_digest'])
    else:route['image_routes']['tiktok:LH_MY'][0]['url']='https://images.example/foreign.png'
    with pytest.raises(ValueError,match='GALLERY_'):validate_gallery_documents(docs,route)
