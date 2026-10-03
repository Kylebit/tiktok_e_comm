import json
import pytest
from test_publication_r2_review import registered
from test_publication_r2_candidate import candidate
from test_publication_takeover import packet
from shared_platform.product_workspace_evidence import history, product_evidence


def files(root):
    return {str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}


def local(root, offer, title):
    path=root/'data/new_product_workbench'/f'{offer}.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps({'offer_id':offer,'_revision':1,'review':{'title':title,'seller_sku':'0099'}}),encoding='utf-8')


def test_registered_and_local_history_coexist_deduplicate_without_writes(registered):
    root,_=registered
    local(root,'123','Superseded local title')
    local(root,'456','Another real product')
    before=files(root)
    value=history(root=root)
    rows={r['offer_id']:r for r in value['items']}
    assert len(value['items'])==2 and set(rows)=={'123','456'}
    assert rows['123']['record_source']=='registered_review'
    assert rows['123']['title']!='Superseded local title'
    assert rows['456']['record_source']=='local_archive'
    assert all(r['execution_authority'] is False for r in rows.values())
    evidence=product_evidence('123',root=root)
    assert evidence['record_source']=='registered_review'
    assert all(r['status']=='AVAILABLE' for r in evidence['rounds'])
    assert evidence['r2_review']['keep_count']==0
    assert files(root)==before


@pytest.mark.parametrize('damage',['root','projection','candidate','incomplete'])
def test_invalid_registration_never_falls_back_to_same_offer_old_archive(registered,damage):
    root,original=registered
    local(root,'123','Must not masquerade as connected')
    directory=root/'data/r2_candidate_reviews/123'
    path=directory/'registration.json'
    if damage=='root':
        value=json.loads(path.read_bytes());value['runtime_root']=str(root/'old-root')
        path.write_text(json.dumps(value),encoding='utf-8')
    elif damage=='projection':
        (directory/'project/data/new_product_workbench/123.json').write_text('{}',encoding='utf-8')
    elif damage=='candidate':
        original[2].write_text('{}',encoding='utf-8')
    else:path.unlink()
    before=files(root)
    result=history(root=root)
    assert result['items']==[] and result['errors'][0]['offer_id']=='123'
    assert files(root)==before


def test_missing_archive_is_not_discovered_or_created(tmp_path):
    assert history(root=tmp_path)['items']==[]
    assert not list(tmp_path.iterdir())


def test_master_display_requires_exact_recorded_identity_and_bytes(candidate,tmp_path):
    from pathlib import Path
    from shared_platform.publication_r2_review import register_candidate, master_image_bytes
    from test_publication_r2_candidate import sha
    root,directory,binding,*_=candidate
    path=directory/'brand-image-generation.json'
    value=json.loads(path.read_bytes())
    image=directory/'source.png'
    value['assets'][0]['artifact_path']=str(image)
    path.write_text(json.dumps(value),encoding='utf-8')
    runtime=tmp_path/'viewer'
    register_candidate(runtime_root=runtime,source_root=root,offer_id='123',binding_path=binding,expected_sha256=sha(binding))
    before=files(runtime)
    assert master_image_bytes('123','1',sha(binding),runtime_root=runtime)==image.read_bytes()
    for offer,number,digest in [('456','1',sha(binding)),('123','2',sha(binding)),('123','1','0'*64)]:
        with pytest.raises(ValueError):master_image_bytes(offer,number,digest,runtime_root=runtime)
    assert files(runtime)==before
    image.write_bytes(b'changed')
    with pytest.raises(ValueError):master_image_bytes('123','1',sha(binding),runtime_root=runtime)


@pytest.mark.parametrize('kind',['outside','traversal','symlink','project_report'])
def test_master_allowlist_cannot_be_replaced_by_arbitrary_local_file(candidate,tmp_path,kind):
    from shared_platform.publication_r2_review import register_candidate, master_image_bytes, registered_project
    from test_publication_r2_candidate import sha
    root,directory,binding,*_=candidate
    image=directory/'source.png'
    outside=root/'unrelated.png';outside.write_bytes(image.read_bytes())
    path=directory/'brand-image-generation.json';value=json.loads(path.read_bytes())
    if kind=='outside':target=outside
    elif kind=='traversal':target=directory/'..'/directory.name/'source.png'
    elif kind=='symlink':
        target=directory/'linked.png';target.symlink_to(image)
    else:target=image
    value['assets'][0]['artifact_path']=str(target)
    path.write_text(json.dumps(value),encoding='utf-8')
    runtime=tmp_path/'viewer'
    register_candidate(runtime_root=runtime,source_root=root,offer_id='123',binding_path=binding,expected_sha256=sha(binding))
    if kind=='project_report':
        projected=registered_project('123',runtime_root=runtime)/'reports/product-preparation/123/brand-image-generation.json'
        value['assets'][0]['review_number']=99
        value['assets'][0]['artifact_path']=str(outside)
        projected.write_text(json.dumps(value),encoding='utf-8')
        with pytest.raises(ValueError):master_image_bytes('123','99',sha(binding),runtime_root=runtime)
        assert master_image_bytes('123','1',sha(binding),runtime_root=runtime)==image.read_bytes()
    else:
        with pytest.raises(ValueError):master_image_bytes('123','1',sha(binding),runtime_root=runtime)
