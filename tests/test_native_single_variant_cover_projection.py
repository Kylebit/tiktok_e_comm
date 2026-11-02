"""One native Model, seven applicable roles, one frozen cover hero; zero live IO."""
from copy import deepcopy
import hashlib
import pytest

from shared_platform import publication_r3_image_bridge as bridge
from shared_platform.r3_technical_category_evidence import prepare_technical_category_supplement
from test_round1_workspace_freeze import live
from test_r3_technical_category_evidence import _completed, _evidence, _manifest


def test_original_single_model_full_gallery_has_one_cover_without_reopening_frozen_rounds(live, monkeypatch, tmp_path):
    from test_b4b_release_compiler import policy, INCIDENTS
    from domains.product_operations.approved_publication_snapshot import build_publication_preview
    from shared_platform.ozon_runtime_credentials import PinnedOzonCredentials
    from shared_platform.ozon_stock_warehouse_decision import resolve_warehouse_decision
    from shared_platform.native_new_task_v4_targets import local_execution_capabilities
    from shared_platform import publication_autopilot
    from shared_platform.postpublish_promotions import build_approved_postpublish_promotion_policy
    store, common, payload, signing, run_id = _completed(live,monkeypatch,tmp_path,single_variant_gallery=True)
    documents = bridge.load_r2_documents(payload['product_id']); original_docs = deepcopy(documents)
    before_db = store.path.read_bytes(); original_common = deepcopy(store.get_plan(common['plan_id']))
    refs = _evidence(tmp_path/'safe-metadata',documents,signing_config=signing)
    ozon = refs['discovery'].parent/'ozon.local.json'
    monkeypatch.setenv('ORBIT_OZON_CREDENTIALS_PATH',str(ozon))
    monkeypatch.setenv('ORBIT_OZON_CREDENTIALS_SHA256',hashlib.sha256(ozon.read_bytes()).hexdigest())
    monkeypatch.setenv('ORBIT_OZON_EXPECTED_ACCOUNT_ID','12345')
    capture = _manifest(store,documents,common,run_id,refs,refs['discovery'].parent)
    prepare_technical_category_supplement(store,documents=documents,common_plan_id=common['plan_id'],
        common_run_id=run_id,capture_manifest=capture)
    warehouse = resolve_warehouse_decision(offer_id=payload['product_id'],round1=documents['round1_snapshot'],
        pinned=PinnedOzonCredentials('12345','owned-secret','a'*64),
        post_bound=lambda *a,**k:{'warehouses':[dict(warehouse_id=71,status='active',is_kgt=False)]})
    market = bridge.build_marketplace_stage_payload(documents,store.get_plan(common['plan_id']),store.get_run(run_id),
        policy=policy(40),incidents=INCIDENTS,ozon_stock_decision=warehouse['decision'],category_store=store,
        promotion_policy=build_approved_postpublish_promotion_policy(approval_reference='owned-final-candidate-preview'))
    projection = market['r3_marketplace_binding']['variant_image_projection']
    assert len(projection['variants'])==1 and projection['variants'][0]['role']=='cover'
    assert projection['single_variant_role_selection']['basis']=='UNIQUE_FROZEN_COVER_OF_ONE_MODEL'
    assert len(projection['single_variant_role_selection']['applicable_roles'])==7
    assert len(market['images'])==7
    snapshot = build_publication_preview(market)
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        evidence = bridge.marketplace_quality_evidence(snapshot,market,category_store=store,category_connection=db)
    candidate = publication_autopilot.compile_release_preview(snapshot,policy=policy(40),
        incident_registry=INCIDENTS,durable_evidence=evidence)
    bounded_policy=policy(0)
    bounded_policy['write_budgets']={'TIKTOK':{'shared_maximum':1,'per_target_maximum':3},
        'SHOPEE':{'shared_maximum':3,'per_target_maximum':9},'OZON':{'shared_maximum':2,'per_target_maximum':0}}
    candidate=publication_autopilot.compile_release_preview(snapshot,policy=bounded_policy,
        incident_registry=INCIDENTS,durable_evidence=evidence)
    assert not any(b['code']=='write_budget_insufficient' for b in candidate['blockers']),candidate['blockers']
    assert candidate['write_budget_scope']['kind']=='EXACT_UNAPPROVED_R3_CANDIDATE'
    assert candidate['write_budget']['TIKTOK']['shared_maximum']==sum(t.startswith('tiktok:') for t in market['targets'])
    assert candidate['write_budget']['SHOPEE']['shared_maximum']==9
    assert candidate['write_budget']['OZON']['shared_maximum']==2
    assert candidate['write_budget']['TIKTOK']['per_target_maximum']==3
    assert candidate['write_budget']['SHOPEE']['per_target_maximum']>=3
    assert candidate['write_budget']['OZON']['per_target_maximum']==0
    actions=candidate['companion_actions']['actions']
    discounts=[a for a in actions if a['action_kind']=='POSTPUBLISH_DIRECT_DISCOUNT']
    assert len(discounts)==sum(t.startswith(('tiktok:','shopee:')) for t in market['targets'])
    assert all(a['write_budget']['maximum_attempts']==1 and a['write_budget']['no_automatic_retry'] for a in discounts)
    assert any(a['execution_adapter_available'] is False and a['execution_blocker']=='GOVERNED_PROMOTION_TRANSPORT_UNAVAILABLE' for a in discounts)
    assert next(a for a in actions if a['action_kind']=='OZON_FBS_STOCK_CONVERGENCE')['write_budget']['included_in_publication_shared_maximum'] is True
    # Once the exact candidate is saved, policy changes cannot enlarge it.
    frozen_evidence=deepcopy(evidence)
    frozen_evidence['marketplace_candidate_budget_source']['frozen_candidate_facts']=deepcopy(candidate)
    repeated=publication_autopilot.compile_release_preview(snapshot,policy=policy(999),
        incident_registry=INCIDENTS,durable_evidence=frozen_evidence)
    assert repeated['write_budget']==candidate['write_budget']
    assert repeated['write_budget_scope']==candidate['write_budget_scope']
    assert repeated['companion_actions']==candidate['companion_actions']
    invalid=deepcopy(evidence)
    invalid['marketplace_candidate_budget_source']['common_run_id']='another-common-run'
    with pytest.raises(publication_autopilot.PublicationAutopilotContractError,match='budget source conflicts'):
        publication_autopilot.compile_release_preview(snapshot,policy=bounded_policy,
            incident_registry=INCIDENTS,durable_evidence=invalid)
    assert len(candidate['review_manifest']['variants'])==1
    caps = local_execution_capabilities(snapshot,candidate)
    assert all(r['local_adapter_supported'] for r in caps['targets']), caps
    assert documents==original_docs and bridge.load_r2_documents(payload['product_id'])==original_docs
    assert store.path.read_bytes()==before_db and store.get_plan(common['plan_id'])==original_common
    for fault in ('missing-cover','duplicate-cover','foreign-key','missing-artifact','multiple-models'):
        wrong_docs=deepcopy(documents); wrong_payload=deepcopy(market)
        plan=next(b for b in wrong_docs['round1_snapshot']['image_plan']['brand_plans'] if b['id']=='livelyhive-sea')
        if fault=='missing-cover':plan['generated_assets'][0]['role']='not-a-cover'
        elif fault=='duplicate-cover':plan['generated_assets'][1]['role']='cover'
        elif fault=='foreign-key':plan['generated_assets'][1]['variant_keys']=['other-model']
        elif fault=='missing-artifact':
            wrong_docs['generation_result']['assets']=[a for a in wrong_docs['generation_result']['assets']
                if not (a['brand_id']=='livelyhive-sea' and a['role']=='cover')]
        else:
            wrong_payload['sku_lineage']['assignment']['model_skus'].append(dict(variant_key='other-model',model_sku='9999'))
        with pytest.raises(ValueError,match='GENERIC_'):
            bridge._project_generic_frozen_variant_images(wrong_payload,wrong_docs,common,dict(run_id=run_id))
