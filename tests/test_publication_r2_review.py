import copy
import json
from pathlib import Path
import pytest

from test_publication_r2_candidate import candidate,sha
from test_publication_takeover import packet


@pytest.fixture
def registered(candidate,tmp_path):
    from shared_platform.publication_r2_review import register_candidate
    (candidate[1]/'automated-image-qa.json').write_text(json.dumps({'offer_id':'123'}), encoding='utf-8')
    root=tmp_path/'runtime';root.mkdir()
    register_candidate(runtime_root=root,source_root=candidate[0],offer_id='123',
        binding_path=candidate[2],expected_sha256=sha(candidate[2]))
    return root,candidate


def view(root):
    from shared_platform.publication_r2_review import review_view
    return review_view('123',runtime_root=root)


def request(value,action='keep'):
    return {'offer_id':value['offer_id'],'binding_sha256':value['binding_sha256'],'expected_revision':value['revision'],
        'decisions':[{'image_id':r['image_id'],'output_sha256':r['output_sha256'],'targets':r['targets'],'action':action} for r in value['images']]}


def test_registration_is_exact_pending_and_preserves_sources(registered):
    root,original=registered
    before={p:p.read_bytes() for p in original[0].rglob('*') if p.is_file() and not p.is_relative_to(root)}
    value=view(root)
    assert value['offer_id']=='123' and value['keep_count']==0
    assert value['images'][0]['action']=='review'
    assert value['publication_authorized'] is False
    assert value['r2_consumer']['status']=='BLOCKED'
    assert before=={p:p.read_bytes() for p in original[0].rglob('*') if p.is_file() and not p.is_relative_to(root)}


def test_decision_persists_and_duplicate_click_is_idempotent(registered):
    from shared_platform.publication_r2_review import decide,registered_project
    root,_=registered;before=view(root);body=request(before)
    saved=decide(body,runtime_root=root)
    assert saved['keep_count']==1 and saved['revision']==1
    assert view(root)==saved
    assert decide(body,runtime_root=root)==saved
    receipt=registered_project('123',runtime_root=root)/'reports/product-preparation/123/r2-candidate-adoption.json'
    assert json.loads(receipt.read_bytes())['decisions'][0]['action']=='keep'
    assert saved['publication_authorized'] is False and saved['r2_consumer']['status']=='BLOCKED'


@pytest.mark.parametrize('damage',['binding','offer','target','digest','image','incomplete','duplicate','action','revision'])
def test_invalid_decisions_never_write(registered,damage):
    from shared_platform.publication_r2_review import decide
    root,_=registered;value=view(root);body=request(value)
    if damage=='binding':body['binding_sha256']='0'*64
    elif damage=='offer':body['offer_id']='456'
    elif damage=='target':body['decisions'][0]['targets']=['ozon:RU']
    elif damage=='digest':body['decisions'][0]['output_sha256']='f'*64
    elif damage=='image':body['decisions'][0]['image_id']='foreign'
    elif damage=='incomplete':body['decisions']=[]
    elif damage=='duplicate':body['decisions']*=2
    elif damage=='action':body['decisions'][0]['action']='approved'
    elif damage=='revision':body['expected_revision']=True
    before={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}
    with pytest.raises(ValueError):decide(body,runtime_root=root)
    assert before=={p:p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_drift_before_user_click_cannot_be_accepted(registered):
    from shared_platform.publication_r2_review import decide
    root,candidate=registered;body=request(view(root))
    Path(candidate[-1]['output_path']).write_bytes(b'changed')
    with pytest.raises(ValueError):decide(body,runtime_root=root)


def test_old_revision_with_different_decision_does_not_overwrite(registered):
    from shared_platform.publication_r2_review import decide
    root,_=registered;first=view(root)
    decide(request(first),runtime_root=root)
    with pytest.raises(ValueError):decide(request(first,'remove'),runtime_root=root)
    assert view(root)['keep_count']==1


def test_r3_consumer_refuses_pending_and_old_artifacts_after_keep(registered,monkeypatch):
    from shared_platform.publication_r2_review import decide,registered_project
    from shared_platform import publication_r3_image_bridge as bridge
    root,_=registered;project=registered_project('123',runtime_root=root)
    monkeypatch.setattr(bridge,'validate_r2_identity',lambda docs:{'fixture_only':True})
    with pytest.raises(ValueError,match='R2_CANDIDATE_REVIEW_REQUIRED'):
        bridge.load_r2_documents('123',reports_root=project/'reports/product-preparation')
    decide(request(view(root)),runtime_root=root)
    with pytest.raises(ValueError,match='R2_ADOPTED_ASSET_RECEIPT_REQUIRED'):
        bridge.load_r2_documents('123',reports_root=project/'reports/product-preparation')


def test_registration_repeat_is_read_only_and_projection_drift_fails(registered):
    from shared_platform.publication_r2_review import register_candidate,registered_project
    root,candidate=registered
    before={p:(p.read_bytes(),p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()}
    register_candidate(runtime_root=root,source_root=candidate[0],offer_id='123',binding_path=candidate[2],expected_sha256=sha(candidate[2]))
    assert before=={p:(p.read_bytes(),p.stat().st_mtime_ns) for p in root.rglob('*') if p.is_file()}
    state=registered_project('123',runtime_root=root)/'data/new_product_workbench/123.json'
    data=json.loads(state.read_bytes());data['_revision']=8;state.write_text(json.dumps(data),encoding='utf-8')
    with pytest.raises(ValueError,match='PROJECTION_DRIFT'):view(root)


def test_deleting_adoption_or_marker_cannot_reenable_old_r3(registered):
    from shared_platform.publication_r2_review import registered_project
    from shared_platform.publication_r3_image_bridge import load_r2_documents
    root,_=registered;reports=registered_project('123',runtime_root=root)/'reports/product-preparation'
    (reports/'123/r2-candidate-registration.json').unlink()
    with pytest.raises(ValueError,match='REGISTRATION_MISSING'):load_r2_documents('123',reports_root=reports)


def test_concurrent_different_clicks_have_one_winner(registered):
    from concurrent.futures import ThreadPoolExecutor
    from shared_platform.publication_r2_review import decide
    root,_=registered;value=view(root)
    def save(action):
        try:return decide(request(value,action),runtime_root=root)
        except ValueError:return None
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(save,['keep','remove']))
    assert sum(row is not None for row in results)==1
    assert view(root)['revision']==1
