"""Captured real c50 producers; this boundary does not claim a COMMON write."""
from copy import deepcopy
import json
from pathlib import Path
import pytest

from shared_platform.publication_quality_evidence import evaluate_publication_quality_evidence
from shared_platform.publication_rounds import canonical_digest

FIXTURE = Path(__file__).parent / 'fixtures' / 'b4b_r2_actual'

def documents():
    names = {'round1_snapshot':'round1-approved-snapshot','first_review':'first-review',
             'generation_result':'brand-image-generation','translation_plan':'brand-image-translation-plan',
             'translation_result':'brand-image-translation','image_qa':'automated-image-qa'}
    return {key:json.loads((FIXTURE/(name+'.json')).read_text(encoding='utf-8')) for key,name in names.items()}

def evidence_context():
    docs=documents();offer=docs['round1_snapshot']['offer_id']
    snapshot={'offer_id':offer,'plan_id':'test-identity-only','snapshot_digest':'sha256:'+'a'*64,
              'publication_targets':[{'target_label':label} for label in docs['round1_snapshot']['canonical_targets']]}
    # This synthetic handoff exercises QA checking only, never a full candidate or write authority.
    docs['workflow_handoff']={'offer_id':offer,'plan_id':snapshot['plan_id'],'snapshot_digest':snapshot['snapshot_digest']}
    docs['publication_bridge']={'offer_id':offer,'generation_identity_digest':docs['translation_plan']['generation_identity_digest'],
        'translation_plan_digest':canonical_digest(docs['translation_plan']),
        'release_handoff':dict(docs['workflow_handoff'],round1_snapshot_digest=docs['round1_snapshot']['snapshot_digest'])}
    docs['platform_preflight']={}
    return snapshot,docs

@pytest.mark.parametrize('drift',['qa_digest','generation','round1','artifacts'])
def test_same_offer_old_or_modified_qa_is_not_accepted(drift):
    snapshot,docs=evidence_context();qa=docs['image_qa']
    if drift=='qa_digest':qa['qa_digest']='sha256:'+'0'*64
    elif drift=='generation':qa['generation_identity_digest']='sha256:'+'0'*64
    elif drift=='round1':qa['round1_snapshot_digest']='sha256:'+'0'*64
    else:qa['artifact_digests']=qa['artifact_digests'][:-1]
    if drift!='qa_digest':qa.pop('qa_digest');qa['qa_digest']=canonical_digest(qa)
    result=evaluate_publication_quality_evidence(snapshot,docs,pack={})
    check=next(row for row in result['checks'] if row['code']=='AUTOMATED_IMAGE_QA_BOUND')
    assert check['status']=='FAILED'


def test_actual_c50_qa_has_a_complete_current_identity():
    from shared_platform.publication_r3_image_bridge import validate_r2_identity
    source=documents();before=deepcopy(source)
    identity=validate_r2_identity(source)
    assert identity['offer_id']=='9000052'
    assert identity['qa_digest']==source['image_qa']['qa_digest']
    assert len(identity['artifact_digests'])==21
    assert source==before
    snapshot,docs=evidence_context()
    result=evaluate_publication_quality_evidence(snapshot,docs,pack={})
    check=next(row for row in result['checks'] if row['code']=='AUTOMATED_IMAGE_QA_BOUND')
    assert check['status']=='PASSED'
    # Other synthetic handoff facts are incomplete: never claim full final QA passed.
    assert result['status']=='FAILED'


def test_technical_r2_actor_is_accepted_as_image_lineage_only():
    from shared_platform.publication_r3_image_bridge import validate_r2_identity
    from shared_platform.publication_autopilot import (
        PublicationAutopilotContractError, validate_final_approval_receipt,
    )
    docs=documents()
    plan=docs['translation_plan']
    plan.update(approved_by='orbit-product-publication-default-v1',
                status='APPROVED_BY_AUTOPILOT',approval_authority='ACTIVE_AUTOPILOT_POLICY')
    docs['translation_result']['plan_digest']=canonical_digest(plan)
    assert validate_r2_identity(docs)['offer_id']=='9000052'
    with pytest.raises(PublicationAutopilotContractError,match='final approval digest'):
        validate_final_approval_receipt(plan,{'status':'READY_FOR_FINAL_REVIEW'})


@pytest.mark.parametrize('drift',['boolean_number','foreign_actor','wrong_authority'])
def test_actual_producer_number_and_approval_types_are_preserved(drift):
    from shared_platform.publication_r3_image_bridge import validate_r2_identity
    docs=documents()
    if drift=='boolean_number':docs['generation_result']['assets'][0]['review_number']=True
    elif drift=='foreign_actor':docs['translation_plan']['approved_by']='another-actor'
    else:docs['translation_plan']['approval_authority']='unrelated-authority'
    docs['translation_result']['plan_digest']=canonical_digest(docs['translation_plan'])
    with pytest.raises(ValueError,match='R2_'):
        validate_r2_identity(docs)


@pytest.mark.parametrize('drift',['first_review','r1_digest','generation_r1','plan_digest','plan_source','missing_generation'])
def test_real_r2_output_binding_rejects_each_broken_predecessor(drift):
    from shared_platform.publication_r3_image_bridge import validate_r2_identity
    docs=documents()
    if drift=='first_review':docs['first_review']['product_facts']['title']='changed after approval'
    elif drift=='r1_digest':docs['round1_snapshot']['snapshot_digest']='sha256:'+'0'*64
    elif drift=='generation_r1':docs['generation_result']['round1_snapshot_digest']='sha256:'+'0'*64
    elif drift=='plan_digest':docs['translation_result']['plan_digest']='sha256:'+'0'*64
    elif drift=='plan_source':
        docs['translation_plan']['tasks'][0]['source_url']='https://fixture.example/another.png'
        docs['translation_result']['approved_tasks']=deepcopy(docs['translation_plan']['tasks'])
        docs['translation_result']['plan_digest']=canonical_digest(docs['translation_plan'])
    else:docs.pop('generation_result')
    with pytest.raises(ValueError,match='R2_'):
        validate_r2_identity(docs)


def copy_reports(tmp_path):
    directory=tmp_path/'9000052';directory.mkdir()
    for path in FIXTURE.glob('*.json'):
        if path.name!='PROVENANCE.json':(directory/path.name).write_bytes(path.read_bytes())
    return directory


def test_actual_r2_can_prepare_routes_but_cannot_bypass_missing_common_stage(tmp_path):
    from shared_platform import publication_r3_image_bridge as bridge
    directory=copy_reports(tmp_path)
    value=bridge._persist_dual_brand_publication_bridge('9000052',reports_root=tmp_path)
    assert value['status']=='COMMON_STAGE_PLAN_REQUIRED'
    assert len(value['common_miaoshou_baseline'])==7
    assert len(value['image_routes']['tiktok:LH_PH'])==7
    assert all(row['kind']=='APPROVED_MASTER' for row in value['image_routes']['tiktok:LH_PH'])
    assert all(row['kind']=='LOCALIZED_ARTIFACT' for row in value['image_routes']['ozon:RU'])
    assert value['miaoshou_writes']==value['platform_writes']==0
    before=(directory/'dual-brand-publication-handoff.json').read_bytes()
    assert bridge._persist_dual_brand_publication_bridge('9000052',reports_root=tmp_path)==value
    for fn in (bridge.sync_dual_brand_miaoshou_baseline, bridge.finalize_dual_brand_release_handoff):
        with pytest.raises(ValueError,match='R3_LOCAL_REPORTS_NOT_AUTHORITY'):
            fn('9000052',reports_root=tmp_path)
    assert (directory/'dual-brand-publication-handoff.json').read_bytes()==before
    assert not (directory/'workflow-handoff.json').exists()


def test_preparation_does_not_replace_prior_handoff_or_change_approved_actor(tmp_path):
    from shared_platform import publication_r3_image_bridge as bridge
    directory=copy_reports(tmp_path);old=directory/'dual-brand-publication-handoff.json'
    old.write_text('{"prior":"preserve"}',encoding='utf-8')
    with pytest.raises(ValueError,match='R3_BRIDGE_CONFLICT'):
        bridge._persist_dual_brand_publication_bridge('9000052',reports_root=tmp_path)
    assert old.read_text()=='{"prior":"preserve"}'
    with pytest.raises(ValueError,match='R3_APPROVAL_ACTOR_CONFLICT'):
        bridge._persist_dual_brand_publication_bridge('9000052',approved_by='another-actor',reports_root=tmp_path)


@pytest.mark.parametrize('dangling',[False,True])
def test_report_directory_escape_is_rejected_before_missing_checks(tmp_path,dangling):
    from shared_platform import publication_r3_image_bridge as bridge
    root=tmp_path/'reports';root.mkdir();outside=tmp_path/'outside'
    if not dangling:outside.mkdir()
    (root/'9000052').symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError,match='R3_REPORT_PATH_ESCAPES_ROOT'):
        bridge._persist_dual_brand_publication_bridge('9000052',reports_root=root)
    assert not (outside/'dual-brand-publication-handoff.json').exists()


@pytest.mark.parametrize('drift',['qa_digest','generation','round1','artifacts'])
def test_real_compiler_keeps_current_r2_identity_failure_as_a_blocker(drift):
    from domains.product_operations import build_approved_publication_snapshot
    from shared_platform.publication_autopilot import compile_release_candidate
    from test_approved_publication_snapshot import _approved_plan,_rebind
    from test_b4b_release_compiler import policy,INCIDENTS
    _,docs=evidence_context();plan=_approved_plan();payload=plan['payload']
    payload['product_id']='9000052'
    payload['targets']=docs['round1_snapshot']['canonical_targets']
    payload.pop('shopee_global_master')
    for container in (payload['pricing']['selected_targets'],payload['product_facts']['categories_by_target']):
        for label in list(container):
            if label not in payload['targets']:container.pop(label)
    snapshot=build_approved_publication_snapshot(_rebind(plan)).payload()
    for binding in (docs['workflow_handoff'],docs['publication_bridge']['release_handoff']):
        binding.update(plan_id=snapshot['plan_id'],snapshot_digest=snapshot['snapshot_digest'])
    qa=docs['image_qa']
    if drift=='qa_digest':qa['qa_digest']='sha256:'+'0'*64
    elif drift=='generation':qa['generation_identity_digest']='sha256:'+'0'*64
    elif drift=='round1':qa['round1_snapshot_digest']='sha256:'+'0'*64
    else:qa['artifact_digests']=qa['artifact_digests'][:-1]
    if drift!='qa_digest':qa.pop('qa_digest');qa['qa_digest']=canonical_digest(qa)
    candidate=compile_release_candidate(snapshot,policy=policy(),incident_registry=INCIDENTS,durable_evidence=docs)
    assert candidate['status']=='BLOCKED'
    assert any(row['code']=='AUTOMATED_IMAGE_QA_BOUND' for row in candidate['blockers'])


@pytest.mark.parametrize('execute_common,finalize',[(False,False),(True,False),(False,True)])
def test_actual_r3_preparation_entry_rejects_legacy_local_authority(tmp_path,execute_common,finalize):
    import importlib.util
    from argparse import Namespace
    path=Path(__file__).parents[1]/'skills/publish-approved-product/scripts/prepare_publication_execution.py'
    spec=importlib.util.spec_from_file_location('b4b_r3_preparation_entry',path)
    entry=importlib.util.module_from_spec(spec);spec.loader.exec_module(entry)
    directory=copy_reports(tmp_path)
    with pytest.raises(ValueError,match='R3_LOCAL_REPORTS_NOT_AUTHORITY'):
        entry.execute(Namespace(offer_id='9000052',reports_root=tmp_path,
            execute_miaoshou=execute_common,confirm_miaoshou_write=True,finalize_release_handoff=finalize))
    assert not (directory/'round2-image-snapshot.json').exists()
    assert not (directory/'dual-brand-publication-handoff.json').exists()
    assert not (directory/'workflow-handoff.json').exists()
