"""Actual R2 Skill boundary after removing the old mixed R2/R3 dispatcher."""
import sys

import pytest
from test_publication_paid_entry import workflow,FixtureClient,rounds,write,read

def test_chat_approval_does_not_reenter_mixed_round3_dispatcher(workflow):
    with pytest.raises(ValueError,match='round 3'):
        workflow.entry.run(workflow.args(approve_all=True))
    assert not FixtureClient.calls

def test_read_only_status_exposes_required_master_phase(workflow):
    result=workflow.entry.run(workflow.args())
    assert result['status']=='BRAND_IMAGE_GENERATION_REQUIRED'
    assert result['external_generation_count']==result['platform_writes']==0
    assert not (workflow.directory/'paid-requests/events.jsonl').exists()

def test_miaoshou_flag_is_rejected_before_any_mutating_helper(workflow,monkeypatch):
    from modules.sourcing import new_product_workbench as wb
    monkeypatch.setattr(wb,'sync_localized_images_to_miaoshou_before_review',lambda *a,**kw:pytest.fail('R2 reached R3'))
    with pytest.raises(ValueError,match='round 3'):
        workflow.entry.run(workflow.args(execute_miaoshou=True,confirm_miaoshou_write=True))
    assert not FixtureClient.calls

def test_legacy_image_plan_requires_existing_r1_budget_bridge(workflow):
    w=workflow
    w.round1['image_plan']['translation_plan']['status']='LEGACY_APPROVED'
    w.round1.pop('snapshot_digest');w.round1['snapshot_digest']=rounds.canonical_digest(w.round1)
    write(w.directory/'round1-approved-snapshot.json',w.round1)
    result=w.entry.run(w.args())
    assert result['status']=='LEGACY_R2_BRIDGE_REQUIRED' and result['external_generation_count']==0

def test_empty_approved_translation_plan_consumes_no_new_request(workflow):
    w=workflow;w.masters()
    from test_publication_paid_entry import read
    plan=w.entry.build_approved_brand_translation_plan(w.round1['offer_id'],generation=read(w.directory/'brand-image-generation.json'),
        selections={},approved_by='orbit-product-publication-default-v1')
    write(w.directory/'brand-image-translation-plan.json',plan)
    result=w.entry.run(w.args(execute_paid=True))
    assert result['paid_requests']['new_this_invocation']==0 and result['paid_requests']['occupied']==14
    assert len(FixtureClient.calls)==14

def test_finalize_handoff_stays_with_round3_and_preserves_r1(workflow):
    snapshot=(workflow.directory/'round1-approved-snapshot.json').read_bytes()
    with pytest.raises(ValueError,match='round 3'):
        workflow.entry.run(workflow.args(finalize_release_handoff=True))
    assert (workflow.directory/'round1-approved-snapshot.json').read_bytes()==snapshot
    assert not FixtureClient.calls


def test_cli_default_translation_scope_is_technical_not_kyle_approval(workflow, monkeypatch):
    w=workflow
    w.masters()
    write(w.directory/'first-review.json',{
        'status':'FIRST_REVIEW_READY',
        'target_selection':{'requested':w.round1['canonical_targets']},
    })
    paid_before=len(FixtureClient.calls)
    monkeypatch.setattr(sys,'argv',[
        'prepare_product_images.py','--offer-id',w.round1['offer_id'],
        '--approve-translation-images','none',
    ])
    assert w.entry.main()==0
    plan=read(w.directory/'brand-image-translation-plan.json')
    assert plan['approved_by']=='orbit-product-publication-default-v1'
    assert plan['status']=='APPROVED_BY_AUTOPILOT'
    assert plan['approval_authority']=='ACTIVE_AUTOPILOT_POLICY'
    assert len(FixtureClient.calls)==paid_before
    frozen=(w.directory/'brand-image-translation-plan.json').read_bytes()
    assert w.entry.main()==0
    assert (w.directory/'brand-image-translation-plan.json').read_bytes()==frozen
    assert len(FixtureClient.calls)==paid_before


def test_cli_default_does_not_rewrite_existing_kyle_translation_plan(workflow, monkeypatch):
    w=workflow
    w.masters()
    write(w.directory/'first-review.json',{
        'status':'FIRST_REVIEW_READY',
        'target_selection':{'requested':w.round1['canonical_targets']},
    })
    legacy=w.entry.build_approved_brand_translation_plan(
        w.round1['offer_id'],generation=read(w.directory/'brand-image-generation.json'),
        selections={},approved_by='orbit-product-publication-default-v1',
    )
    # Simulate a persisted plan minted by the old builder. The new builder must
    # not mint this attribution, while validation keeps its frozen digest.
    legacy.update(status='APPROVED_IN_CONVERSATION',approved_by='Kyle',approval_authority='USER_FINAL_IMAGE_REVIEW')
    path=w.directory/'brand-image-translation-plan.json'
    write(path,legacy)
    old_bytes=path.read_bytes()
    paid_before=len(FixtureClient.calls)
    monkeypatch.setattr(sys,'argv',[
        'prepare_product_images.py','--offer-id',w.round1['offer_id'],
        '--approve-translation-images','none',
    ])
    assert w.entry.main()==1
    assert path.read_bytes()==old_bytes
    assert len(FixtureClient.calls)==paid_before


def test_explicit_kyle_translation_scope_requires_independent_receipt(workflow, monkeypatch):
    w=workflow
    w.masters()
    generation=read(w.directory/'brand-image-generation.json')
    with pytest.raises(ValueError,match='independent verifiable conversation receipt'):
        w.entry.build_approved_brand_translation_plan(
            w.round1['offer_id'],generation=generation,selections={},approved_by='Kyle',
        )
    write(w.directory/'first-review.json',{
        'status':'FIRST_REVIEW_READY',
        'target_selection':{'requested':w.round1['canonical_targets']},
    })
    path=w.directory/'brand-image-translation-plan.json'
    monkeypatch.setattr(sys,'argv',[
        'prepare_product_images.py','--offer-id',w.round1['offer_id'],
        '--approve-translation-images','none','--approved-by','Kyle',
    ])
    assert w.entry.main()==1
    assert not path.exists()


def test_automatic_reuse_plan_has_technical_actor_and_preserves_existing_bytes(workflow):
    w=workflow
    path=w.directory/'brand-image-reuse-plan.json'
    frozen=path.read_bytes()+b'\n'
    path.write_bytes(frozen)
    reuse=w.entry._autopilot_reuse_plan(w.round1['offer_id'],w.round1['image_plan'])
    assert reuse['status']=='APPROVED'
    assert reuse['approved_by']=='orbit-product-publication-default-v1'
    assert reuse['approval_authority']=='ACTIVE_AUTOPILOT_POLICY'
    w.masters()
    assert path.read_bytes()==frozen


def test_fresh_automatic_reuse_plan_is_written_with_technical_actor(workflow):
    w=workflow
    path=w.directory/'brand-image-reuse-plan.json'
    path.unlink()
    w.masters()
    reuse=read(path)
    assert reuse['approved_by']=='orbit-product-publication-default-v1'
    assert reuse['approval_authority']=='ACTIVE_AUTOPILOT_POLICY'


def test_unverified_historical_kyle_plan_preserves_digest_but_blocks_paid_path(workflow):
    w=workflow
    w.masters()
    generation=read(w.directory/'brand-image-generation.json')
    historical=w.entry.build_approved_brand_translation_plan(
        w.round1['offer_id'],generation=generation,selections={},
        approved_by='orbit-product-publication-default-v1',
    )
    historical.update(status='APPROVED_IN_CONVERSATION',approved_by='Kyle',approval_authority='USER_FINAL_IMAGE_REVIEW')
    frozen_digest=rounds.canonical_digest(historical)
    path=w.directory/'brand-image-translation-plan.json'
    external_path=w.root/'untrusted-kyle-plan.json'
    write(path,historical)
    write(external_path,historical)
    frozen_bytes=path.read_bytes()
    external_bytes=external_path.read_bytes()
    paid_before=len(FixtureClient.calls)
    with pytest.raises(ValueError,match='unverified historical Kyle'):
        w.entry._validate_brand_translation_plan(
            w.round1['offer_id'],plan=historical,generation=generation,
        )
    with pytest.raises(ValueError,match='unverified historical Kyle'):
        w.entry.run(w.args(execute_paid=True,translation_plan=external_path))
    with pytest.raises(ValueError,match='unverified historical Kyle'):
        w.entry.run(w.args(execute_paid=True))
    assert path.read_bytes()==frozen_bytes
    assert external_path.read_bytes()==external_bytes
    assert rounds.canonical_digest(read(path))==frozen_digest
    assert len(FixtureClient.calls)==paid_before


def test_private_builder_cannot_mint_kyle_conversation_approval(workflow):
    w=workflow
    w.masters()
    generation=read(w.directory/'brand-image-generation.json')
    with pytest.raises(ValueError,match='unverified historical Kyle'):
        w.entry._build_brand_translation_plan(
            w.round1['offer_id'],generation=generation,selections={},approved_by='Kyle',
        )
