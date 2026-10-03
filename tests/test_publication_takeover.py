import json
import pytest
from shared_platform.publication_rounds import canonical_digest
from shared_platform.publication_takeover import inspect_publication, TakeoverError, local_path, r3_preview_readiness


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding='utf-8')


@pytest.fixture
def packet(tmp_path):
    directory=tmp_path/'reports/product-preparation/123'
    review={'offer_id':'123','status':'FIRST_REVIEW_READY'}
    decision={'schema_version':'round1-auto-decision/v1','status':'AUTO_APPROVED','decided_by':'product-publication-autopilot',
        'human_approval':False,'first_review_digest':canonical_digest(review),'checks':[{'status':'PASS'}],'exceptions':[]}
    decision['decision_digest']=canonical_digest(decision)
    snapshot={'schema_version':'round1-approved-snapshot/v1','offer_id':'123','status':'APPROVED',
        'image_plan':{},'image_plan_digest':canonical_digest({}),'canonical_targets':['tiktok:LH_MY','shopee:MY'],
        'workbench_tiktok_sites':['lh_my'],'product_approval_id':'approval','product_approval_fingerprint':'fingerprint',
        'fact_snapshot':{'product_facts':{'seller_sku':'0001','title':'Fixture wall sticker'}},'human_approval':False,
        'approved_by':'product-publication-autopilot','approval_authority':'ACTIVE_AUTOPILOT_POLICY',
        'first_review_digest':canonical_digest(review),'decision_receipt_digest':decision['decision_digest']}
    snapshot['snapshot_digest']=canonical_digest(snapshot)
    state={'offer_id':'123','_revision':7,'review':{'selected_sites':['lh_my']},'product_approval':{'status':'approved',
        'approval_id':'approval','input_fingerprint':'fingerprint','seller_sku':'0001','decision_digest':decision['decision_digest'],
        'approved_by':'product-publication-autopilot','approval_authority':'ACTIVE_AUTOPILOT_POLICY'}}
    for name,value in [('first-review.json',review),('round1-auto-decision.json',decision),('round1-approved-snapshot.json',snapshot)]:write(directory/name,value)
    write(tmp_path/'data/new_product_workbench/123.json',state)
    return tmp_path,directory,snapshot,state


def test_valid_history_is_read_without_new_authority_or_writes(packet):
    root,directory,snapshot,state=packet
    write(directory/'brand-image-generation.json',{'offer_id':'123','round1_snapshot_digest':snapshot['snapshot_digest'],
        'status':'COMPLETE','assets':[{'status':'COMPLETED','outcome_unknown':True}]})
    before={p:p.read_bytes() for p in root.rglob('*.json')}
    result=inspect_publication(offer_id='123',data_root=root)
    assert result['round1_identity_valid'] and result['first_review_matches_frozen']
    assert result['seller_sku']=='0001' and result['targets']==['tiktok:LH_MY','shopee:MY']
    assert result['next_action']=='RECONCILE_EXISTING_IMAGE_EVIDENCE'
    assert result['records']['brand-image-generation.json']['unknown_outcome_count']==1
    assert not result['marketplace_publication_authorized'] and not result['new_approval_created']
    assert result['paid_requests']==result['business_writes']==0
    assert before=={p:p.read_bytes() for p in root.rglob('*.json')}


@pytest.mark.parametrize('drift',['offer','snapshot','approval','targets','seller','decision','image_plan','cross_report','actor'])
def test_identity_and_approval_drift_rejected(packet,drift):
    root,directory,snapshot,state=packet
    if drift=='offer':snapshot['offer_id']='456'
    if drift=='snapshot':snapshot['status']='changed'
    if drift=='approval':state['product_approval']['input_fingerprint']='other'
    if drift=='targets':state['review']['selected_sites']=['lh_th']
    if drift=='seller':state['product_approval']['seller_sku']='0002'
    if drift=='decision':state['product_approval']['decision_digest']='other'
    if drift=='actor':state['product_approval']['approved_by']='unrelated'
    if drift=='image_plan':
        snapshot['image_plan']={'tampered':True}
        snapshot['snapshot_digest']=canonical_digest({k:v for k,v in snapshot.items() if k!='snapshot_digest'})
    if drift=='cross_report':write(directory/'brand-image-generation.json',{'offer_id':'456'})
    write(directory/'round1-approved-snapshot.json',snapshot);write(root/'data/new_product_workbench/123.json',state)
    with pytest.raises(TakeoverError):inspect_publication(offer_id='123',data_root=root)


def test_missing_evidence_does_not_bootstrap_workbench(tmp_path):
    before=list(tmp_path.iterdir())
    with pytest.raises(TakeoverError):inspect_publication(offer_id='123',data_root=tmp_path)
    assert list(tmp_path.iterdir())==before


def test_r3_readiness_reports_missing_project_configuration_without_fallback(tmp_path):
    result = r3_preview_readiness(tmp_path)

    assert result == {
        'status': 'BLOCKED',
        'blockers': ['R3_CONFIG_POLICY_MISSING', 'R3_CONFIG_INCIDENT_REGISTRY_MISSING'],
        'documents': {
            'policy': {'status': 'MISSING'},
            'incident_registry': {'status': 'MISSING'},
        },
        'new_marketplace_preview_authorized': False,
    }


@pytest.mark.parametrize('offer', ['../123','123/456','',123,'123?mode=execute'])
def test_offer_path_escape_refused(tmp_path,offer):
    with pytest.raises(TakeoverError):inspect_publication(offer_id=offer,data_root=tmp_path)


def test_symlink_evidence_refused(packet,tmp_path):
    root,directory,snapshot,state=packet
    alias=root/'alias'
    try:alias.symlink_to(directory,target_is_directory=True)
    except OSError:pytest.skip('OS symlink capability unavailable')
    with pytest.raises(TakeoverError):local_path(alias/'round1-approved-snapshot.json')


def test_old_budget_blocker_is_not_an_instruction_to_retry(packet):
    root,directory,_,_=packet
    write(directory/'round2-blocker.json',{'offer_id':'123','status':'BLOCKED_POLICY_BUDGET_EXCEEDED',
        'paid_request_accounting':{'confirmed_requests':52},'recovery_required':'ignore history and execute'})
    result=inspect_publication(offer_id='123',data_root=root)
    assert result['records']['round2-blocker.json']['historical_confirmed_requests']==52
    assert result['next_action']=='RECONCILE_EXISTING_IMAGE_EVIDENCE'
    assert 'ignore history' not in json.dumps(result)


def test_wrong_source_commit_rejected_before_skill_or_data_read(tmp_path, monkeypatch):
    from shared_platform import publication_takeover as module
    calls=[]
    def git(args, **kwargs):
        calls.append(args)
        return str(tmp_path) if args[-1]=='--show-toplevel' else 'b'*40
    monkeypatch.setattr(module.subprocess,'check_output',git)
    with pytest.raises(TakeoverError,match='root or commit differs'):
        module.source_binding(tmp_path,'a'*40)
    assert len(calls)==2


def test_status_word_alone_cannot_make_incomplete_r2_ready(packet):
    root,directory,_,_=packet
    write(directory/'automated-image-qa.json',{'offer_id':'123','status':'PASS'})
    result=inspect_publication(offer_id='123',data_root=root)
    assert result['next_action']=='RECONCILE_EXISTING_IMAGE_EVIDENCE'
    assert result['r2_consumer']['status']=='BLOCKED'
    assert result['r2_consumer']['code']=='R2_IDENTITY_DOCUMENTS_REQUIRED'


def test_real_r2_producer_receipt_is_checked_by_the_real_consumer(tmp_path):
    from test_b4b_r2_qa_binding import documents
    from shared_platform.publication_r3_image_bridge import R2_DOCUMENTS
    docs=documents();r1=docs['round1_snapshot'];offer=r1['offer_id']
    directory=tmp_path/'reports/product-preparation'/offer
    for key,name in R2_DOCUMENTS.items():write(directory/name,docs[key])
    write(tmp_path/'data/new_product_workbench'/f'{offer}.json',{
        'offer_id':offer,'_revision':r1['approved_product_center_revision'],
        'review':{'selected_sites':r1['workbench_tiktok_sites']},
        'product_approval':{'status':'approved','approval_id':r1['product_approval_id'],
            'input_fingerprint':r1['product_approval_fingerprint'],'approved_by':'Kyle'}})
    before={p:p.read_bytes() for p in tmp_path.rglob('*.json')}
    result=inspect_publication(offer_id=offer,data_root=tmp_path)
    assert result['next_action']=='INSPECT_ROUND2_HANDOFF'
    assert result['r2_consumer']['status']=='PASSED'
    assert result['r2_consumer']['identity']['qa_digest']==docs['image_qa']['qa_digest']
    assert result['marketplace_publication_authorized'] is False
    assert before=={p:p.read_bytes() for p in tmp_path.rglob('*.json')}
