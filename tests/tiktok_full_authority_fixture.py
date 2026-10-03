"""Real ten-target constructors, synthetic identities, no filesystem or IO."""
from copy import deepcopy


def full_authority():
    from test_b4b_release_compiler import neutral_product_plan, policy, INCIDENTS
    from test_approved_publication_snapshot import _category_decision, _rebind, _warehouse_allocations
    from domains.product_operations import build_approved_publication_snapshot
    from shared_platform.tiktok_lineage_recovery import TIKTOK_TARGET_ORDER
    from shared_platform.publication_autopilot import compile_release_candidate, build_final_approval_receipt
    plan = neutral_product_plan()
    payload = plan['payload']
    payload.pop('shopee_global_master', None)
    payload['targets'] = list(reversed(TIKTOK_TARGET_ORDER))
    facts = payload['product_facts']
    facts['categories_by_target'] = {label:_category_decision(label,'600338','Decoration','600001','Home') for label in payload['targets']}
    prices = deepcopy(payload['pricing']['selected_targets']['tiktok:LH_PH'])
    payload['pricing']['selected_targets'] = {label:deepcopy(prices) for label in payload['targets']}
    template = _warehouse_allocations()['tiktok:LH_PH']
    facts['warehouse_inventory_by_target'] = {label:deepcopy(template) for label in payload['targets']}
    for index, allocation in enumerate(facts['warehouse_inventory_by_target'].values()):
        allocation['shop_id'] = str(99000000 + index)
    value = build_approved_publication_snapshot(_rebind(plan)).payload()
    candidate = compile_release_candidate(value, policy=policy(12), incident_registry=INCIDENTS, platform_scope=('TIKTOK',))
    assert candidate['status'] == 'READY_FOR_FINAL_REVIEW', candidate['blockers']
    approval = build_final_approval_receipt(candidate, approved_by='Kyle', execution_snapshot=value)
    return value, candidate, approval
