"""Pending local corrections remain review evidence, never publication assets."""
import hashlib
import json
from pathlib import Path
import pytest

from test_publication_takeover import packet, write
from shared_platform.publication_rounds import canonical_digest
from shared_platform.publication_takeover import inspect_publication, TakeoverError


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def candidate(packet):
    from PIL import Image
    root,directory,r1,state=packet
    state['_revision']=r1['approved_product_center_revision']=7
    r1['snapshot_digest']=canonical_digest({k:v for k,v in r1.items() if k!='snapshot_digest'})
    write(directory/'round1-approved-snapshot.json',r1)
    write(root/'data/new_product_workbench/123.json',state)
    folder=root/'candidates';folder.mkdir()
    source=directory/'source.png';output=folder/'output.png'
    Image.new('RGB',(8,8),'white').save(source)
    im=Image.open(source);im.putpixel((0,7),(0,0,0));im.save(output)
    protected=hashlib.sha256(Image.open(source).convert('RGBA').crop((0,0,8,6)).tobytes()).hexdigest()
    master={'review_number':1,'brand_id':'livelyhive-sea','role':'piece_layout_or_instructions',
        'artifact_digest':'sha256:'+sha(source),'public_url':'https://fixture.example/master.png'}
    task={'review_number':1,'brand_id':master['brand_id'],'role':master['role'],
        'locale':'ms-MY','source_artifact_digest':master['artifact_digest'],'source_url':master['public_url']}
    write(directory/'brand-image-generation.json',{'offer_id':'123','assets':[master]})
    write(directory/'brand-image-translation-plan.json',{'offer_id':'123','tasks':[task],'approved_task_count':1})
    write(directory/'brand-image-translation.json',{'offer_id':'123','assets':[
        {'source_review_number':1,'brand_id':master['brand_id'],'role':master['role'],'locale':'ms-MY',
         'provider_task_id':42,'artifact_digest':'sha256:'+'b'*64}]})
    row={'review_number':1,'brand_id':master['brand_id'],'locale':'ms-MY',
        'source_path':str(source),'source_sha256':sha(source),'output_path':str(output),'output_sha256':sha(output),
        'protected_region':[0,0,8,6],'protected_region_sha256':protected,'footer_region':[0,6,8,8],
        'footer_text':'8 cm setiap','provider_calls':0}
    manifest=folder/'MANIFEST.json'
    write(manifest,{'schema_version':'deterministic-footer-localization-candidate/v1','offer_id':'123',
        'status':'VISUAL_QA_REQUIRED','rows':[row]})
    binding={'schema_version':'r2-localization-candidate-binding/v1','offer_id':'123','revision':7,
        'status':'PENDING_USER_VISUAL_QA','approved_fact_snapshot_sha256':r1['product_approval_fingerprint'],
        'round1_snapshot_sha256':sha(directory/'round1-approved-snapshot.json'),
        'translation_plan_sha256':sha(directory/'brand-image-translation-plan.json'),
        'candidate_code_commit':'a'*40,'candidate_manifest':{'path':str(manifest),'sha256':sha(manifest)},
        'accepted_actions':{'user_keep':False,'formal_report_write':False,'workbench_write':False,
            'miaoshou_write':False,'marketplace_write':False},
        'routes':[{'review_number':1,'brand_id':master['brand_id'],'role':master['role'],'locale':'ms-MY',
            'master_sha256':sha(source),'output':'output.png','output_sha256':sha(output),
            'protected_region_sha256':protected,'historical_provider_task_id':42}]}
    path=folder/'CANDIDATE_BINDING.json';write(path,binding)
    return root,directory,path,binding,manifest,row


def inspect(candidate):
    from shared_platform.publication_r2_candidate import inspect_candidate
    root,_,path,_,_,_=candidate
    return inspect_candidate(binding_path=path,expected_sha256=sha(path),
        takeover=inspect_publication(offer_id='123',data_root=root))


def test_pending_candidate_is_verified_without_promotion_or_writes(candidate):
    before={p:p.read_bytes() for p in candidate[0].rglob('*') if p.is_file()}
    result=inspect(candidate)
    assert result['status']=='PENDING_USER_VISUAL_QA'
    assert result['candidate_asset_count']==1 and result['keep_count']==0
    assert result['execution_authority'] is False and result['r3_ready'] is False
    assert result['routes'][0]['targets']==['tiktok:LH_MY','shopee:MY']
    assert 'public_url' not in json.dumps(result)
    assert before=={p:p.read_bytes() for p in candidate[0].rglob('*') if p.is_file()}


@pytest.mark.parametrize('drift',['binding_hash','offer','revision','fact','round1','plan','manifest',
    'duplicate','foreign_locale','master','output','source_bytes','output_bytes','keep','task'])
def test_candidate_identity_and_pending_boundary_fail_closed(candidate,drift):
    from shared_platform.publication_r2_candidate import inspect_candidate
    root,_,path,binding,manifest,row=candidate
    expected=sha(path)
    if drift=='binding_hash':expected='0'*64
    elif drift=='offer':binding['offer_id']='456'
    elif drift=='revision':binding['revision']=8
    elif drift=='fact':binding['approved_fact_snapshot_sha256']='changed'
    elif drift=='round1':binding['round1_snapshot_sha256']='0'*64
    elif drift=='plan':binding['translation_plan_sha256']='0'*64
    elif drift=='manifest':binding['candidate_manifest']['sha256']='0'*64
    elif drift=='duplicate':binding['routes']*=2
    elif drift=='foreign_locale':binding['routes'][0]['locale']='th-TH'
    elif drift=='master':binding['routes'][0]['master_sha256']='0'*64
    elif drift=='output':binding['routes'][0]['output']='../source.png'
    elif drift=='keep':binding['accepted_actions']['user_keep']=True
    elif drift=='task':binding['routes'][0]['historical_provider_task_id']=43
    elif drift=='source_bytes':Path(row['source_path']).write_bytes(b'changed')
    elif drift=='output_bytes':Path(row['output_path']).write_bytes(b'changed')
    if drift!='binding_hash':write(path,binding);expected=sha(path)
    with pytest.raises(TakeoverError):
        inspect_candidate(binding_path=path,expected_sha256=expected,
            takeover=inspect_publication(offer_id='123',data_root=root))


def test_candidate_linked_image_is_rejected(candidate):
    root,_,path,binding,manifest,row=candidate
    original=Path(row['output_path']);alias=original.with_name('alias.png')
    alias.symlink_to(original)
    data=json.loads(manifest.read_bytes());data['rows'][0]['output_path']=str(alias)
    write(manifest,data);binding['candidate_manifest']['sha256']=sha(manifest)
    binding['routes'][0]['output']='alias.png';write(path,binding)
    with pytest.raises(TakeoverError):inspect(candidate)


def test_actual_cli_binds_pending_candidate_and_preserves_formal_blocker(candidate):
    import os
    import subprocess
    import sys
    root=Path(__file__).resolve().parents[1]
    before={p:p.read_bytes() for p in candidate[0].rglob('*') if p.is_file()}
    head=subprocess.check_output(['git','-C',str(root),'rev-parse','HEAD'],text=True).strip()
    result=subprocess.run([sys.executable,'-B',str(root/'scripts/publication_takeover.py'),
        '--expected-commit',head,'--data-root',str(candidate[0]),'--offer-id','123',
        '--r2-candidate-binding',str(candidate[2]),'--expected-candidate-sha256',sha(candidate[2])],
        cwd=root,env=dict(os.environ,PYTHONIOENCODING='ascii'),capture_output=True)
    assert result.returncode==0, result.stderr.decode('utf-8',errors='replace')
    value=json.loads(result.stdout.decode('utf-8'))
    assert value['pending_r2_candidate']['keep_count']==0
    assert value['r2_consumer']['status']=='BLOCKED'
    assert value['marketplace_publication_authorized'] is False
    assert value['source_binding']['runtime_files']['shared_platform/publication_r2_candidate.py']==sha(
        root/'shared_platform/publication_r2_candidate.py')
    assert before=={p:p.read_bytes() for p in candidate[0].rglob('*') if p.is_file()}


def test_changed_evidence_between_takeover_and_candidate_is_rejected(candidate):
    from shared_platform.publication_r2_candidate import inspect_candidate
    root,directory,path,_,_,_=candidate
    takeover=inspect_publication(offer_id='123',data_root=root)
    write(directory/'brand-image-generation.json',{'offer_id':'123','assets':[]})
    with pytest.raises(TakeoverError,match='EVIDENCE_CHANGED'):
        inspect_candidate(binding_path=path,expected_sha256=sha(path),takeover=takeover)


def test_rehashed_output_cannot_hide_changed_product_artwork(candidate):
    from PIL import Image
    _,_,path,binding,manifest,row=candidate
    output=Path(row['output_path'])
    with Image.open(output) as image:
        changed=image.copy()
    changed.putpixel((2,2),(255,0,0));changed.save(output)
    data=json.loads(manifest.read_bytes());data['rows'][0]['output_sha256']=sha(output)
    write(manifest,data)
    binding['routes'][0]['output_sha256']=sha(output)
    binding['candidate_manifest']['sha256']=sha(manifest);write(path,binding)
    with pytest.raises(TakeoverError,match='ARTWORK_CHANGED'):inspect(candidate)


@pytest.mark.parametrize('footer_end,changed_y,error',[
    (7,6,None), (7,7,'OUTSIDE_FOOTER_CHANGED'), (8,7,None), (7,5,'ARTWORK_CHANGED'),
])
def test_footer_boxes_are_half_open_even_after_rebinding(candidate,footer_end,changed_y,error):
    from PIL import Image
    _,_,path,binding,manifest,row=candidate
    output=Path(row['output_path'])
    with Image.open(row['source_path']) as source:
        changed=source.copy()
    changed.putpixel((2,changed_y),(255,0,0));changed.save(output)
    data=json.loads(manifest.read_bytes())
    data['rows'][0]['footer_region'][3]=footer_end
    data['rows'][0]['output_sha256']=sha(output)
    write(manifest,data)
    binding['routes'][0]['output_sha256']=sha(output)
    binding['candidate_manifest']['sha256']=sha(manifest);write(path,binding)
    if error:
        with pytest.raises(TakeoverError,match=error):inspect(candidate)
    else:
        assert inspect(candidate)['keep_count']==0
