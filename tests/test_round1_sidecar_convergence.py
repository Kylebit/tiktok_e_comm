from copy import deepcopy
import importlib.util
import json
from pathlib import Path
import pytest


@pytest.fixture
def client():
    path=Path(__file__).resolve().parents[1]/'skills/prepare-product-publication/scripts/prepare_product_publication.py'
    spec=importlib.util.spec_from_file_location('r1_sidecar_client',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def image_plan():
    return {'schema_version':'first-review-image-plan/v1','status':'PROPOSED','source_actions':[],
        'generated_assets':[],'summary':{'translation_positions':[],'localized_output_count':0,
        'net_new_output_count':0,'paid_generation_required':False}}


def candidate(target='shopee:MY'):
    return {'schema_version':'first-review-candidate-plan/v1','target_candidates':[{'target':target,
        'category':{'status':'PROPOSED','candidate':'Fixture decoration','authority':'untrusted sidecar',
        'evidence_digest':'sha256:'+'a'*64,'note':'Requires official observation'},
        'copy':{'status':'PROPOSED','language':'ms','title':'Fixture title','description':'Fixture description',
        'specification_name':'Size','variants':[{'seller_sku':'0001','display_name':'20 cm'}]}}],
        'content_group_options':[]}


def test_candidate_copy_never_substitutes_for_official_category(client):
    result=client.prepare_offer(offer_id='123',requested_targets=['shopee:MY'],
        preview_builder=lambda _:dict(revision=7,review={'selected_sites':['shopee:MY']}),
        image_execution_plan=image_plan(),candidate_plan=candidate())
    assert result['status']=='DECISION_REQUIRED'
    assert 'CATEGORY_RECEIPT_UNAVAILABLE' in result['blockers']
    assert result['category_evidence_binding']['status']=='DECISION_REQUIRED'
    assert result['targets'][0]['copy']['title']=='Fixture title'
    assert result['targets'][0]['copy']['status']=='PROPOSED'
    assert result['miaoshou_sync']['status']=='DEFERRED_TO_THIRD_ROUND'
    assert result['external_write_count']==0


@pytest.mark.parametrize('drift',['wrong_target','duplicate','approval','audit_copy'])
def test_candidate_validation_preserves_scope_and_no_approval(client,drift):
    plan=candidate()
    if drift=='wrong_target':plan['target_candidates'][0]['target']='shopee:TH'
    if drift=='duplicate':plan['target_candidates']*=2
    if drift=='approval':plan['target_candidates'][0]['copy']['status']='APPROVED'
    if drift=='audit_copy':plan['target_candidates'][0]['copy']['title']='Must not be inferred'
    with pytest.raises(client.PreparationError):client._safe_candidate_plan(plan,['shopee:MY'])


def test_standard_sidecars_survive_cli_refresh_without_arguments(client,tmp_path,monkeypatch,capsys):
    monkeypatch.setattr(client,'REPO_ROOT',tmp_path)
    directory=tmp_path/'reports/product-preparation/123';directory.mkdir(parents=True)
    plans={'first-review-image-plan.json':image_plan(),'first-review-candidate-plan.json':candidate('tiktok:LH_MY')}
    for name,value in plans.items():(directory/name).write_text(json.dumps(value))
    monkeypatch.setattr(client,'_default_preview_builder',lambda:lambda _:dict(revision=7,review={'selected_sites':['lh_my']}))
    before={name:(directory/name).read_bytes() for name in plans}
    assert client.main(['--offer-id','123','--targets','tiktok:LH_MY'])==0
    report=json.loads((directory/'first-review.json').read_text())
    assert report['image_execution_plan']==plans['first-review-image-plan.json']
    assert report['targets'][0]['copy']['title']=='Fixture title'
    assert before=={name:(directory/name).read_bytes() for name in plans}


def test_dual_brand_target_scope_never_produces_shared_single_group(client):
    targets=['tiktok:LH_MY','tiktok:HB_MY','shopee:MY','ozon:RU']
    groups=client._content_group_projection(targets,[])['groups']
    assert groups[0]['id']=='livelyhive-sea' and groups[0]['targets']==['tiktok:LH_MY','shopee:MY','ozon:RU']
    assert groups[1]['id']=='homebloom-sea' and groups[1]['targets']==['tiktok:HB_MY']
    single=client._content_group_projection(['tiktok:LH_MY'],[])
    assert not any(g['id']=='homebloom-sea' for g in single['groups'])


@pytest.mark.parametrize('offer',['../123','..\\123','C:/123','１２３','123/456'])
def test_cli_rejects_invalid_offer_before_resolving_sidecars(client,monkeypatch,capsys,offer):
    touched=[]
    def unexpected_path(value):
        touched.append(value)
        raise AssertionError('invalid identity reached filesystem path construction')
    monkeypatch.setattr(client,'_default_output_path',unexpected_path)
    assert client.main(['--offer-id',offer,'--targets','tiktok:LH_MY'])==2
    assert touched==[]
    assert json.loads(capsys.readouterr().out)['kind']=='PreparationError'


@pytest.mark.parametrize('targets,recommended',[
    (['tiktok:LH_MY'],'homebloom-sea'),
    (['tiktok:HB_MY'],'livelyhive-sea'),
    (['tiktok:LH_MY'],'split-by-brand'),
    (['tiktok:HB_MY','shopee:MY'],'homebloom-sea'),
    (['tiktok:HB_MY','shopee:MY'],'livelyhive-sea'),
])
def test_recommendation_cannot_override_target_brand(client,targets,recommended):
    plan=client._safe_candidate_plan({'schema_version':'first-review-candidate-plan/v1',
        'target_candidates':[], 'content_group_options':[{'id':recommended,'label':'Candidate',
        'description':'Proposed content scope','recommended':True}]},targets)
    result=client._content_group_projection(targets,plan['content_group_options'])
    assert result['status']=='USER_DECISION_REQUIRED'
    assert result['groups']==[]


@pytest.mark.parametrize('targets,group',[
    (['tiktok:LH_MY','shopee:MY'],'livelyhive-sea'),
    (['tiktok:HB_MY'],'homebloom-sea'),
])
def test_recommendation_must_match_all_explicit_targets(client,targets,group):
    result=client._content_group_projection(targets,[{'id':group,'label':'Candidate','recommended':True}])
    assert result['status']=='SELECTED_BY_EXPLICIT_TARGET_SCOPE'
    assert result['groups'][0]['id']==group
    assert result['groups'][0]['targets']==targets
