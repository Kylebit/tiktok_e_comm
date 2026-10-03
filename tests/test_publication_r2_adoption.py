import hashlib,json
from copy import deepcopy
from pathlib import Path
import pytest
from PIL import Image
from shared_platform import publication_r2_adoption as adoption
from shared_platform import publication_r2_review as review
from shared_platform.publication_rounds import canonical_digest
from shared_platform.publication_r3_image_bridge import R2_DOCUMENTS,_brand_generation_identity_digest,load_r2_documents
from shared_platform.publication_image_qa import build_automated_image_qa,ASSESSMENT_SCHEMA
from test_b4b_r2_qa_binding import documents

def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(review._bytes(value))
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()

@pytest.fixture
def ready(tmp_path):
    docs=documents();r1=docs['round1_snapshot'];offer=r1['offer_id'];source=tmp_path/'source'
    directory=source/'reports/product-preparation'/offer;directory.mkdir(parents=True)
    generated=docs['generation_result'];translated=docs['translation_result'];plan=docs['translation_plan']
    masters={}
    for i,asset in enumerate(generated['assets']):
        path=directory/f'master-{i}.png';Image.new('RGB',(8,8),(i*8,10,20)).save(path)
        asset.update(artifact_path=str(path),artifact_digest='sha256:'+sha(path));masters[asset['review_number']]=asset
    identity=_brand_generation_identity_digest(generated)
    plan['generation_identity_digest']=translated['generation_identity_digest']=identity
    for task in plan['tasks']:
        master=masters[task['review_number']];task.update(source_artifact_digest=master['artifact_digest'],source_url=master['public_url'])
    translated.update(plan_digest=canonical_digest(plan),approved_tasks=deepcopy(plan['tasks']),status='BLOCKED_POLICY_BUDGET_EXCEEDED',external_generation_count=52,
        policy_blocker_report={'confirmed_requests':52,'maximum_confirmed_requests_per_product':40})
    folder=source/'candidate';folder.mkdir();routes=[];rows=[];uploads={}
    for i,asset in enumerate(translated['assets']):
        master=masters[asset['source_review_number']];path=folder/f'repair-{i}.png'
        with Image.open(master['artifact_path']) as img:
            img.putpixel((0,7),(230,i,10));img.save(path)
        with Image.open(master['artifact_path']) as img:protected=hashlib.sha256(img.convert('RGBA').crop((0,0,8,6)).tobytes()).hexdigest()
        asset['provider_task_id']=100+i
        key={'review_number':asset['source_review_number'],'brand_id':asset['brand_id'],'locale':asset['locale']}
        rows.append({**key,'source_path':master['artifact_path'],'source_sha256':master['artifact_digest'][7:],
            'output_path':str(path),'output_sha256':sha(path),'protected_region':[0,0,8,6],'footer_region':[0,6,8,8],
            'protected_region_sha256':protected,'footer_text':'synthetic footer'})
        routes.append({**key,'role':asset['role'],'master_sha256':master['artifact_digest'][7:],'output':path.name,
            'output_sha256':sha(path),'protected_region_sha256':protected,'historical_provider_task_id':100+i})
        uploads['local-footer:'+sha(path)]={'artifact_digest':'sha256:'+sha(path),'public_url':f'https://fixture.invalid/upload-{i}.png',
            'upload_receipt_ref':f'fixture-upload:{i}','readback_path':str(path),'readback_sha256':sha(path)}
    for key,name in R2_DOCUMENTS.items():write(directory/name,docs[key])
    write(source/'data/new_product_workbench'/f'{offer}.json',{'offer_id':offer,'_revision':r1['approved_product_center_revision'],
        'review':{'selected_sites':r1['workbench_tiktok_sites']},'product_approval':{'status':'approved','approval_id':r1['product_approval_id'],
            'input_fingerprint':r1['product_approval_fingerprint'],'approved_by':'Kyle'}})
    manifest=folder/'manifest.json';write(manifest,{'schema_version':'deterministic-footer-localization-candidate/v1','offer_id':offer,'status':'VISUAL_QA_REQUIRED','rows':rows})
    binding=folder/'binding.json';write(binding,{'schema_version':'r2-localization-candidate-binding/v1','offer_id':offer,'revision':r1['approved_product_center_revision'],
        'status':'PENDING_USER_VISUAL_QA','approved_fact_snapshot_sha256':r1['product_approval_fingerprint'],
        'round1_snapshot_sha256':sha(directory/'round1-approved-snapshot.json'),'translation_plan_sha256':sha(directory/'brand-image-translation-plan.json'),
        'candidate_code_commit':'a'*40,'candidate_manifest':{'path':str(manifest),'sha256':sha(manifest)},'routes':routes,
        'accepted_actions':dict.fromkeys(['user_keep','formal_report_write','workbench_write','miaoshou_write','marketplace_write'],False)})
    runtime=tmp_path/'runtime';runtime.mkdir()
    view=review.register_candidate(runtime_root=runtime,source_root=source,offer_id=offer,binding_path=binding,expected_sha256=sha(binding))
    body={'offer_id':offer,'binding_sha256':view['binding_sha256'],'expected_revision':0,
        'decisions':[{k:r[k] for k in ['image_id','output_sha256','targets']}|{'action':'keep'} for r in view['images']]}
    review.decide(body,runtime_root=runtime)
    upload_path=tmp_path/'uploads.json';write(upload_path,{'uploaded_assets':uploads})
    return runtime,offer,upload_path,tmp_path/'qa',source

def prepare(ready):
    root,offer,uploads,out,_=ready
    return adoption.prepare_adoption(runtime_root=root,offer_id=offer,expected_revision=1,uploaded_assets_path=uploads,output_dir=out)

def qa(ready,result):
    out=ready[3];docs={key:json.loads((out/name).read_bytes()) for key,name in R2_DOCUMENTS.items()}
    assessment={'schema_version':ASSESSMENT_SCHEMA,'offer_id':ready[1],'artifact_digests':result['artifact_digests'],
        'checks':[{'code':c,'status':'PASSED'} for c in ['FACTUAL_ALIGNMENT','OCR_LANGUAGE','DUPLICATION']]}
    assessment['assessment_digest']=canonical_digest(assessment);write(out/'assessment.json',assessment)
    receipt=build_automated_image_qa(round1_snapshot=docs['round1_snapshot'],generation=docs['generation_result'],translation=docs['translation_result'],visual_assessment=assessment)
    write(out/'new-qa.json',receipt)
    return dict(bundle_path=result['bundle_path'],qa_path=out/'new-qa.json',qa_sha256=sha(out/'new-qa.json'),assessment_path=out/'assessment.json')

def test_real_consumer_adopts_local_repairs_with_history_and_idempotency(ready):
    root,offer,_,_,source=ready;before={p:p.read_bytes() for p in source.rglob('*') if p.is_file()}
    result=prepare(ready);assert result['status']=='QA_REQUIRED'
    project=review.registered_project(offer,runtime_root=root)
    with pytest.raises(ValueError):load_r2_documents(offer,reports_root=project/'reports/product-preparation')
    args=qa(ready,result);receipt=adoption.adopt_prepared(**args)
    assert receipt==adoption.adopt_prepared(**args)
    docs=load_r2_documents(offer,reports_root=project/'reports/product-preparation')
    translated=docs['translation_result'];assert translated['external_generation_count']==52
    assert translated['policy_blocker_report']['maximum_confirmed_requests_per_product']==40
    assert all('provider_task_id' not in a and a['local_provenance']['superseded_asset']['provider_task_id'] for a in translated['assets'])
    assert before=={p:p.read_bytes() for p in source.rglob('*') if p.is_file()}
    assert review.review_view(offer,runtime_root=root)['r2_consumer']['status']=='PASSED'

@pytest.mark.parametrize('drift',['upload','qa','decision','asset'])
def test_drift_never_activates_documents(ready,drift):
    result=prepare(ready);args=qa(ready,result)
    root,offer,uploads,out,_=ready
    project=review.registered_project(offer,runtime_root=root);reports=project/'reports/product-preparation'/offer
    before=(reports/'brand-image-translation.json').read_bytes()
    if drift=='upload':uploads.write_bytes(b'{}')
    if drift=='qa':args['qa_sha256']='0'*64
    if drift=='decision':
        view=review.review_view(offer,runtime_root=root)
        review.decide({'offer_id':offer,'binding_sha256':view['binding_sha256'],'expected_revision':1,
            'decisions':[{k:r[k] for k in ['image_id','output_sha256','targets']}|{'action':'remove'} for r in view['images']]},runtime_root=root)
    if drift=='asset':
        translation=json.loads((out/'brand-image-translation.json').read_bytes());Path(translation['assets'][0]['artifact_path']).write_bytes(b'changed')
    with pytest.raises(ValueError):adoption.adopt_prepared(**args)
    assert (reports/'brand-image-translation.json').read_bytes()==before


def test_official_transcode_requires_both_qa_and_uses_delivery_digest(ready):
    root,offer,uploads,out,source=ready
    manifest=json.loads(uploads.read_bytes())
    for i,entry in enumerate(manifest['uploaded_assets'].values()):
        path=uploads.parent/f'delivery-{i}.jpg'
        with Image.open(entry['readback_path']) as image:image.resize((4,4)).save(path,format='JPEG')
        entry.update(readback_path=str(path),readback_sha256=sha(path),transformation='official-media-transcode',
            dimensions={'source':[8,8],'delivered':[4,4],'format':'JPEG'})
    write(uploads,manifest);result=prepare(ready);args=qa(ready,result)
    with pytest.raises(ValueError,match='ORIGINAL_QA_REQUIRED'):adoption.adopt_prepared(**args)
    original=json.loads((out/'reviewed-original-translation.json').read_bytes())
    generation=json.loads((out/'brand-image-generation.json').read_bytes());r1=json.loads((out/'round1-approved-snapshot.json').read_bytes())
    assessment={'schema_version':ASSESSMENT_SCHEMA,'offer_id':offer,
        'artifact_digests':sorted({a['artifact_digest'] for a in generation['assets']+original['assets']}),
        'checks':[{'code':c,'status':'PASSED'} for c in ['FACTUAL_ALIGNMENT','OCR_LANGUAGE','DUPLICATION']]}
    assessment['assessment_digest']=canonical_digest(assessment);write(out/'original-assessment.json',assessment)
    receipt=build_automated_image_qa(round1_snapshot=r1,generation=generation,translation=original,visual_assessment=assessment)
    write(out/'original-qa.json',receipt)
    adopted=adoption.adopt_prepared(**args,original_qa_path=out/'original-qa.json',original_assessment_path=out/'original-assessment.json')
    assert adopted['r2_identity']['artifact_digests']==result['artifact_digests']
    active=load_r2_documents(offer,reports_root=review.registered_project(offer,runtime_root=root)/'reports/product-preparation')
    assert all(a['artifact_digest']!=a['local_provenance']['reviewed_artifact_digest'] for a in active['translation_result']['assets'])


def test_interrupted_activation_blocks_consumer_and_resumes_without_rewrite(ready,monkeypatch):
    result=prepare(ready);args=qa(ready,result);original_replace=adoption.os.replace
    def crash(source,target):
        if Path(target).name=='automated-image-qa.json':raise OSError('synthetic power loss')
        return original_replace(source,target)
    monkeypatch.setattr(adoption.os,'replace',crash)
    with pytest.raises(OSError):adoption.adopt_prepared(**args)
    root,offer,*_=ready;reports=review.registered_project(offer,runtime_root=root)/'reports/product-preparation'
    with pytest.raises(ValueError,match='RECEIPT_REQUIRED'):load_r2_documents(offer,reports_root=reports)
    monkeypatch.setattr(adoption.os,'replace',original_replace)
    assert adoption.adopt_prepared(**args)['publication_authorized'] is False
    assert load_r2_documents(offer,reports_root=reports)['image_qa']['status']=='PASSED'
