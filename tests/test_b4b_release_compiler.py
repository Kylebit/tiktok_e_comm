"""Real approved snapshot -> release compiler -> execution budget regressions."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from domains.product_operations import build_approved_publication_snapshot, validate_approved_publication_snapshot
from shared_platform.publication_autopilot import (
    PublicationAutopilotContractError,
    build_final_approval_receipt,
    compile_release_candidate,
    validate_final_approval_receipt,
)
from shared_platform.publication_write_budget import PublicationWriteBudgetLedger
from shared_platform.postpublish_promotions import (
    build_approved_postpublish_promotion_policy,
)
from test_approved_publication_snapshot import _approved_plan, _rebind, _sha, _warehouse_allocations


def policy(cap=8):
    return {'schema_version':'autopilot-policy/v1','policy_id':'offline-explicit-policy','status':'ACTIVE',
        'automatic_steps':['final_release_candidate_compilation'],
        'authority':{'kind':'explicit_conversation_approval','approved_by':'offline fixture','approved_at':'2026-09-05','scope':'Synthetic offline validation'},
        'paid_models':{'provider':'lingshi','maximum_confirmed_requests_per_product':40,
            'allowed_purposes':['brand_image_generation'],'automatic_paid_retry':True,
            'maximum_automatic_paid_retries_per_task':3,'unknown_outcome_policy':'STOP_AND_RECONCILE',
            'retry_requires':['KNOWN_FAILED_OUTCOME','EXACT_FAILED_ASSET','DURABLE_RETRY_ATTEMPT']},
        'final_marketplace_publish':{'requires_user_approval':True,'approval_kind':'FINAL_MARKETPLACE_PUBLISH'},
        'write_budgets':{name:{'shared_maximum':cap,'per_target_maximum':9} for name in ['TIKTOK','SHOPEE','OZON']}}


INCIDENTS={'schema_version':'publication-incident-registry/v1','incidents':[]}


def neutral_product_plan():
    # These two-image controls exercise budgets/schema versions, not a family
    # whose approved image contract requires seven images. Wall-sticker gates
    # have their own real compiler regressions in quality_evidence_identity.
    plan=_approved_plan()
    facts=plan['payload']['product_facts']
    facts['title']='Decorative household item'
    facts['description']='Two color variants for household decoration.'
    facts['category']={'id':'other-household','name':'Household decoration'}
    return plan


def snapshot(*, warehouses=False):
    plan=neutral_product_plan()
    if warehouses:
        plan['payload']['product_facts']['warehouse_inventory_by_target']=_warehouse_allocations()
    return build_approved_publication_snapshot(_rebind(plan)).payload()


@pytest.mark.parametrize('platform,scope',[('OZON',('ozon:RU',)),('TIKTOK',('tiktok:LH_PH',))])
def test_compile_subset_projects_full_approved_warehouse_map(platform,scope):
    value=snapshot(warehouses=True);before=deepcopy(value)
    candidate=compile_release_candidate(value,policy=policy(),incident_registry=INCIDENTS,
        platform_scope=(platform,),target_scope=scope)
    assert candidate['status']=='READY_FOR_FINAL_REVIEW', candidate['blockers']
    assert candidate['target_labels']==list(scope)
    assert candidate['warehouse_inventory_policy']['target_labels']==([scope[0]] if platform=='TIKTOK' else [])
    assert value==before
    assert validate_approved_publication_snapshot(value).payload()==before


def test_final_review_states_warehouse_quantity_is_per_model_sku():
    value=snapshot(warehouses=True)
    candidate=compile_release_candidate(value,policy=policy(),incident_registry=INCIDENTS)
    target=next(row for row in candidate['review_manifest']['targets'] if row['target_label']=='tiktok:LH_PH')
    assert target['inventory']['quantity_basis']=='PER_MODEL_SKU'
    assert target['inventory']['total_stock_per_model_sku']==540
    assert target['inventory']['model_skus']==['0958','0959']
    assert value['product']['warehouse_inventory_by_target']['tiktok:LH_PH']['total_stock']==540


def test_final_review_binds_ozon_stock_and_same_approval_continuations():
    plan = neutral_product_plan()
    stock_policy = {
        'schema_version': 'publication-default-stock/v1',
        'quantity_per_sku': 200,
        'scope': 'EACH_SELECTED_SKU',
        'source': 'SYSTEM_GOVERNED_DEFAULT',
        'review_round': 'ROUND1',
    }
    plan['payload']['product_facts']['stock_policy'] = stock_policy
    plan['payload']['product_facts']['ozon_stock_decision'] = {
        'schema_version': 'ozon-stock-warehouse-decision/v1',
        'warehouse_id': 123456,
        'selection_policy': 'EXACT_UNIQUE_ACTIVE_OR_CREATED_NON_KGT',
        'stock_policy_digest': _sha(stock_policy),
        'source': 'OFFICIAL_PROVIDER_READBACK',
    }
    plan['payload']['approved_postpublish_promotion_policy'] = (
        build_approved_postpublish_promotion_policy(
            approval_reference='final-review-fixture'
        )
    )
    value = build_approved_publication_snapshot(_rebind(plan)).payload()
    candidate = compile_release_candidate(
        value,
        policy=policy(),
        incident_registry=INCIDENTS,
        platform_scope=('OZON',),
    )

    assert candidate['status'] == 'READY_FOR_FINAL_REVIEW', candidate['blockers']
    action = candidate['companion_actions']['actions'][0]
    assert action == {
        'action_kind': 'OZON_FBS_STOCK_CONVERGENCE',
        'prerequisite_target': 'ozon:RU',
        'quantity_basis': 'PER_MODEL_SKU',
        'quantity_per_sku': 200,
        'warehouse_selection_policy': 'EXACT_UNIQUE_ACTIVE_OR_CREATED_NON_KGT',
        'warehouse_identity_bound': True,
        'warehouse_source': 'OFFICIAL_PROVIDER_READBACK',
        'provider_identity_resolution': 'FROZEN_ID_REVALIDATED_AT_EXECUTION',
        'completion_authority': 'EXACT_WAREHOUSE_SCOPED_READBACK',
    }
    continuation = candidate['companion_actions']['continuation_authority']
    assert 'ASYNC_PROVIDER_CONVERGENCE' in continuation['without_new_human_approval']
    assert 'SYSTEM_DEFECT_CONTINUATION_WITH_EXACT_APPROVED_LINEAGE' in (
        continuation['without_new_human_approval']
    )
    assert 'STOCK_QUANTITY_OR_WAREHOUSE_POLICY_CHANGE' in (
        continuation['requires_new_human_approval']
    )
    receipt = build_final_approval_receipt(
        candidate, approved_by='Kyle', execution_snapshot=value
    )
    assert receipt['companion_actions_digest']
    drifted = deepcopy(candidate)
    drifted['companion_actions']['actions'][0]['quantity_per_sku'] = 199
    with pytest.raises(PublicationAutopilotContractError):
        validate_final_approval_receipt(receipt, drifted, snapshot=value)

    all_targets = compile_release_candidate(
        value, policy=policy(), incident_registry=INCIDENTS
    )
    promotion_actions = [
        row for row in all_targets['companion_actions']['actions']
        if row['action_kind'] == 'POSTPUBLISH_DIRECT_DISCOUNT'
    ]
    assert {row['prerequisite_target'] for row in promotion_actions} == {
        'tiktok:LH_PH', 'tiktok:LH_MY', 'shopee:PH'
    }


@pytest.mark.parametrize('platform', ['TIKTOK','SHOPEE','OZON'])
def test_compiler_never_expands_policy_shared_hard_cap(platform):
    candidate=compile_release_candidate(snapshot(),policy=policy(0),incident_registry=INCIDENTS,platform_scope=(platform,))
    assert candidate['write_budget'][platform]['shared_maximum']==0
    assert candidate['status']=='BLOCKED'
    assert any(row['code']=='write_budget_insufficient' for row in candidate['blockers'])


def test_exact_offer_write_budget_override_does_not_expand_other_offers():
    governed = policy(0)
    governed['write_budget_overrides'] = {
        '123456': {
            'TIKTOK': {'shared_maximum': 10, 'per_target_maximum': 3},
            'SHOPEE': {'shared_maximum': 9, 'per_target_maximum': 9},
            'OZON': {'shared_maximum': 2, 'per_target_maximum': 0},
        }
    }
    approved = snapshot()
    exact_offer = compile_release_candidate(
        approved, policy=governed, incident_registry=INCIDENTS,
        platform_scope=('OZON',),
    )
    assert exact_offer['write_budget_scope'] == {
        'kind': 'DEFAULT_POLICY', 'offer_id': None,
    }
    assert exact_offer['write_budget']['OZON']['shared_maximum'] == 0

    governed['write_budget_overrides'][approved['offer_id']] = governed[
        'write_budget_overrides'
    ].pop('123456')
    exact_offer = compile_release_candidate(
        approved, policy=governed, incident_registry=INCIDENTS,
        platform_scope=('OZON',),
    )
    assert exact_offer['write_budget_scope'] == {
        'kind': 'EXACT_OFFER_OVERRIDE', 'offer_id': approved['offer_id'],
    }
    assert exact_offer['write_budget']['OZON']['shared_maximum'] == 2


def test_offer_395_freezes_exact_shared_budgets_and_preserves_defaults():
    governed = policy(0)
    governed['write_budgets'] = {
        'TIKTOK': {'shared_maximum': 1, 'per_target_maximum': 3},
        'SHOPEE': {'shared_maximum': 3, 'per_target_maximum': 9},
        'OZON': {'shared_maximum': 2, 'per_target_maximum': 0},
    }
    governed['write_budget_overrides'] = {
        '3956742887': {
            'TIKTOK': {'shared_maximum': 10, 'per_target_maximum': 3},
            'SHOPEE': {'shared_maximum': 9, 'per_target_maximum': 9},
            'OZON': {'shared_maximum': 2, 'per_target_maximum': 0},
        }
    }
    original = snapshot()
    for platform, expected in [('TIKTOK', 10), ('SHOPEE', 9), ('OZON', 2)]:
        candidate = compile_release_candidate(
            original, policy={
                **governed,
                'write_budget_overrides': {original['offer_id']: governed['write_budget_overrides']['3956742887']},
            },
            incident_registry=INCIDENTS, platform_scope=(platform,),
        )
        assert candidate['write_budget'][platform]['shared_maximum'] == expected
    assert governed['write_budgets'] == {
        'TIKTOK': {'shared_maximum': 1, 'per_target_maximum': 3},
        'SHOPEE': {'shared_maximum': 3, 'per_target_maximum': 9},
        'OZON': {'shared_maximum': 2, 'per_target_maximum': 0},
    }


def test_production_policy_scopes_exact_offer_395_budget():
    governed = json.loads(
        (Path(__file__).resolve().parents[1] / 'config' / 'product_publication_autopilot_policy.json')
        .read_text(encoding='utf-8')
    )
    assert governed['write_budget_overrides'] == {
        '3956742887': {
            'TIKTOK': {'shared_maximum': 10, 'per_target_maximum': 3},
            'SHOPEE': {'shared_maximum': 9, 'per_target_maximum': 9},
            'OZON': {'shared_maximum': 2, 'per_target_maximum': 0},
        }
    }
    assert governed['write_budgets'] == {
        'TIKTOK': {'shared_maximum': 1, 'per_target_maximum': 3},
        'SHOPEE': {'shared_maximum': 3, 'per_target_maximum': 9},
        'OZON': {'shared_maximum': 2, 'per_target_maximum': 0},
    }


@pytest.mark.parametrize('cached',[False,True])
def test_real_compiler_ledger_and_checkpointed_upload_count_cached_zero_missing_two(tmp_path,cached):
    from modules.shopee.global_v4_live_runtime import OfficialShopeeGlobalV4Runtime
    from modules.shopee.global_v4_executor import ShopeeGlobalV4Resolver, project_shopee_global_v4_command
    from test_shopee_global_v4_executor import _Runtime
    value=snapshot()
    candidate=compile_release_candidate(value,policy=policy(4),incident_registry=INCIDENTS,platform_scope=('SHOPEE',))
    assert candidate['status']=='READY_FOR_FINAL_REVIEW',candidate['blockers']
    assert candidate['write_budget']['SHOPEE']['required_shared_mutations_uncached']==4
    ledger=PublicationWriteBudgetLedger(platform='SHOPEE',target_labels=('shopee:PH',),budget=candidate['write_budget']['SHOPEE'])
    request=SimpleNamespace(run_id='offline-compiler-budget',report_id='publication-report:offline-compiler-budget',
        platform='SHOPEE',target_labels=('shopee:PH',),snapshot=value,release_candidate=candidate,write_budget_ledger=ledger)
    uploads=[]
    live=OfficialShopeeGlobalV4Runtime(context_resolver=lambda _: {},official_fact_reader=lambda *_: {},
        mapping_lookup=lambda _:None,image_upload_transport=lambda url,pos: uploads.append(url) or 'image-'+str(pos),checkpoint_root=tmp_path)
    command=project_shopee_global_v4_command(value)
    live.lookup_global_item_ids(command)
    if cached:
        live.checkpointed_upload_global_images(request,tuple(value['product']['images']))
        uploads.clear()
        request.write_budget_ledger=PublicationWriteBudgetLedger(platform='SHOPEE',target_labels=('shopee:PH',),budget=candidate['write_budget']['SHOPEE'])
    runtime=_Runtime()
    def upload_with_provider_readback(*args):
        result=live.checkpointed_upload_global_images(*args)
        runtime.image_bindings=deepcopy(result[0])
        return result
    runtime.checkpointed_upload_global_images=upload_with_provider_readback
    resolver=ShopeeGlobalV4Resolver(runtime=runtime)
    assert resolver(request)=='9001'
    assert len(uploads)==(0 if cached else 2)
    assert resolver.write_count(request)==(2 if cached else 4)
    assert request.write_budget_ledger.total_attempt_count==resolver.write_count(request)


def shopee_snapshot(version):
    from test_approved_publication_snapshot import _sha
    plan=neutral_product_plan()
    master=plan['payload']['shopee_global_master']
    category={'status':'APPROVED','category':{'id':'101','name':'Wallpaper',
        'path':[{'id':'101','name':'Wallpaper'}]},'required_attributes':[],
        'source_decision_digest':'sha256:'+'8'*64}
    category['decision_digest']=_sha({'schema_version':'shopee-global-category-decision/v1',**category})
    master['category_decision']=category
    if version=='v2':
        master['schema_version']='shopee-global-master/v2'
        master.pop('variant_image_positions')
        master['variant_image_bindings']=[{'model_sku':sku,'image_url':f'https://img.example/variant-{sku}.jpg',
            'image_digest':'sha256:'+str(index)*64,'source':{'kind':'MIAOSHOU_SOURCE_IMAGE',
                'source_offer_id':plan['payload']['product_id'],'source_position':index}}
            for index,sku in enumerate(('0958','0959'),1)]
    return build_approved_publication_snapshot(_rebind(plan)).payload()


@pytest.mark.parametrize('version',['v1','v2'])
def test_real_frozen_master_versions_compile_and_preflight_without_rebinding(version):
    from shared_platform.publication_preflight import build_platform_preflight
    value=shopee_snapshot(version);before=deepcopy(value)
    candidate=compile_release_candidate(value,policy=policy(),incident_registry=INCIDENTS,platform_scope=('SHOPEE',))
    assert candidate['status']=='READY_FOR_FINAL_REVIEW',candidate['blockers']
    evidence={'publication_bridge':{'status':'MIAOSHOU_VERIFIED'},
        'workflow_handoff':{'snapshot_digest':value['snapshot_digest']},
        'image_qa':{'schema_version':'automated-image-qa/v1','status':'PASSED'}}
    result=build_platform_preflight(value,evidence=evidence)
    assert result['status']=='PASSED',result['checks']
    assert value==before


@pytest.mark.parametrize('version',['v1','v2'])
@pytest.mark.parametrize('drift',['digest','model'])
def test_invalid_frozen_master_cannot_pass_compile_or_preflight(version,drift):
    from shared_platform.publication_preflight import build_platform_preflight
    from shared_platform.publication_autopilot import PublicationAutopilotContractError
    value=shopee_snapshot(version)
    if drift=='digest':
        value['snapshot_digest']='sha256:'+'f'*64
    else:
        key='variant_image_positions' if version=='v1' else 'variant_image_bindings'
        value['shopee_global_master'][key][0]['model_sku']='foreign-sku'
    with pytest.raises(PublicationAutopilotContractError):
        compile_release_candidate(value,policy=policy(),incident_registry=INCIDENTS,platform_scope=('SHOPEE',))
    evidence={'publication_bridge':{'status':'MIAOSHOU_VERIFIED'},
        'workflow_handoff':{'snapshot_digest':value['snapshot_digest']},
        'image_qa':{'schema_version':'automated-image-qa/v1','status':'PASSED'}}
    result=build_platform_preflight(value,evidence=evidence)
    assert result['status']=='FAILED'
    assert next(row for row in result['checks'] if row['code']=='SNAPSHOT_AND_MIAOSHOU_READBACK')['status']=='FAILED'


def test_compile_rejects_target_not_in_selected_platform():
    candidate=compile_release_candidate(snapshot(),policy=policy(),incident_registry=INCIDENTS,
        platform_scope=('OZON',),target_scope=('shopee:PH',))
    assert candidate['status']=='BLOCKED'
    assert candidate['target_labels']==[]
