"""Server-owned zero-write native review preflight; no authority from package JSON."""
from contextlib import closing
import json,os,re,sqlite3
from pathlib import Path


def normal_new_task(runtime):
    operations=runtime._operations
    return (getattr(operations,'new_task_worker',None) is not None
        and getattr(operations,'original_continuation',None) is None
        and getattr(operations,'agent_prepared_common',None) is None)


def inventory_readback_checks(snapshot,block):
    """Only exact returned rows are observable; never fill omitted rows as zero."""
    from shared_platform.native_tiktok_exact_readback import NativeTikTokExactStorefrontReadback
    contract=NativeTikTokExactStorefrontReadback.local_observation_contract()
    inventory=snapshot['product'].get('warehouse_inventory_by_target') or {};checked=[]
    for target in snapshot['publication_targets']:
        if target['platform'].lower()!='tiktok':continue
        label=target['target_label'];allocation=inventory.get(label)
        if allocation is None:
            block('TIKTOK_COMPLETE_WAREHOUSE_SCOPE_NOT_FROZEN',label);status='BLOCKED'
        elif any(row['stock']==0 for row in allocation['warehouses']) and not contract['omitted_zero_quantity_proven']:
            block('TIKTOK_ZERO_WAREHOUSE_READBACK_UNSUPPORTED',label);status='BLOCKED'
        else:status='SUPPORTED_EXPLICIT_COMPLETE_ROWS'
        checked.append({'target_label':label,'status':status,'reader_schema':contract['schema_version'],
            'provider_access':'NOT_OBSERVED'})
    return checked


def _schemas_and_unknown(db,plan,block):
    from shared_platform.native_final_mutation_budget import _DurableLedger,NativeMutationBudgetHold
    try:_DurableLedger._check_schema(None,db)
    except NativeMutationBudgetHold:block('NATIVE_MUTATION_BUDGET_SCHEMA_NOT_INSTALLED');return []
    unknown=[dict(row) for row in db.execute('''SELECT s.plan_id,r.scope_key,r.sequence,
        r.target_label,r.operation,d.request_digest FROM native_final_budget_reservations r
        JOIN native_final_budget_scopes s USING(scope_key)
        JOIN release_plans p ON p.plan_id=s.plan_id
        LEFT JOIN native_final_budget_dispatches d USING(scope_key,sequence)
        LEFT JOIN native_final_budget_outcomes o USING(scope_key,sequence)
        WHERE p.product_id=? AND o.outcome IS NULL''',(plan['product_id'],))]
    for row in unknown:block('ORIGINAL_MUTATION_UNKNOWN_RECONCILIATION_REQUIRED',row['target_label'])
    return unknown


def preflight_native_review(runtime,plan_id):
    """Service-owned, zero-write full dependency gate before the one review.

    Uses original domain graph/COMMON reader and installed schemas. Caller JSON
    and observation booleans cannot replace these reads or create authority.
    No HTTP, SDK, token renewal, paid operation, DDL or cached decision here.
    """
    from shared_platform.publication_execution_package import _need,_asset_checks,digest,NativePublicationPreflightBlocked
    from shared_platform.native_sole_final_service import NativeSoleFinalService
    from shared_platform.r3_frozen_review_producer import read_stored_domain_graph
    from shared_platform.r3_common_source_facts import NativeCommonSourceReader
    from domains.product_operations.approved_publication_snapshot import build_publication_preview
    from shared_platform.publication_r3_image_bridge import marketplace_quality_evidence
    _need(type(runtime) is NativeSoleFinalService,'NATIVE_REVIEW_RUNTIME_REQUIRED')
    plan=runtime.store.get_plan(plan_id)
    _need(plan is not None,'NATIVE_REVIEW_ORIGINAL_PLAN_REQUIRED')
    payload=plan['payload'];blockers=[]
    def block(code,target=None):
        row={'code':code,**({'target_label':target} if target else {})}
        if row not in blockers:blockers.append(row)
    if normal_new_task(runtime):
        from shared_platform.native_new_task_v4_targets import preparation_scope
        try:preparation_scope(runtime,plan)
        except (ValueError,KeyError,TypeError,OSError):block('NATIVE_REVIEW_ORIGINAL_TASK_OR_ROUNDS_UNVERIFIED')
    with closing(runtime.store._connect_readonly()) as db,db:
        db.execute('BEGIN')
        graph=read_stored_domain_graph(db,plan_id,native_reader=NativeCommonSourceReader(runtime.store))
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        snapshot=build_publication_preview(payload)
        evidence=marketplace_quality_evidence(snapshot,payload,category_store=runtime.store,category_connection=db)
        unknown=_schemas_and_unknown(db,plan,block)
    candidate=json.loads(graph.display_bytes)
    for check in evidence['platform_preflight']['checks']:
        if check['status']!='PASSED':block(check['code'],check.get('target_label'))
    documents=payload['r3_marketplace_binding']['documents']
    assets=[]
    rows=list(documents['generation_result'].get('assets',[]))+list(documents['translation_result'].get('assets',[]))
    for row in rows:
        path=row.get('artifact_path') or row.get('local_path')
        if not path:block('R2_LOCAL_IMAGE_ARTIFACT_MISSING');continue
        assets.append({'url':row.get('public_url') or row.get('result_url'),'path':path,'sha256':row.get('artifact_digest'),
                       'role':row.get('role'),'brand':row.get('brand_id')})
    _asset_checks(snapshot,assets,block)
    observable=inventory_readback_checks(snapshot,block)
    from shared_platform.native_new_task_v4_targets import local_execution_capabilities
    capabilities=local_execution_capabilities(snapshot,candidate)
    for target in capabilities['targets']:
        for gap in target['blockers']:block(gap['code'],target['target_label'])
    from shared_platform.postpublish_promotions import approved_promotion_action_policy
    actions=(candidate.get('companion_actions') or {}).get('actions') or []
    for action in actions:
        if action.get('action_kind')=='POSTPUBLISH_DIRECT_DISCOUNT':
            try:approved_promotion_action_policy(payload,action['action_target'])
            except (ValueError,KeyError,TypeError):block('DISCOUNT_CONSUMER_FACTS_INCOMPLETE',action.get('prerequisite_target'))
    if any(a.get('action_kind')=='POSTPUBLISH_DIRECT_DISCOUNT' for a in actions):
        required={'oneclick_release_jobs','oneclick_release_targets','oneclick_release_events','oneclick_release_outcomes',
                  'oneclick_release_manual_acceptances','oneclick_release_write_occurrences'}
        if not required<=tables:block('ONECLICK_SCHEMA_NOT_INSTALLED')
    # Prove the same startup-selected catalog and canonical Model identity that
    # the new-task adapter will consume, before asking for human approval.
    if normal_new_task(runtime):
        from core import db as catalog
        bound=os.environ.get('ORBIT_CATALOG_DATABASE')
        if (not bound or not Path(bound).is_absolute() or not Path(bound).is_file()
                or Path(bound).resolve()!=catalog.db_path().resolve()):block('CATALOG_STARTUP_BINDING_REQUIRED')
        if any(not re.fullmatch(r'[0-9]{4}',str(r.get('model_sku',''))) for r in snapshot['skus']):
            block('CANONICAL_ALIAS_AUTHORITY_REQUIRED')
    report={'schema_version':'native-publication-preflight/v1','offer_id':plan['product_id'],'plan_id':plan_id,
        'candidate_digest':graph.candidate_digest,'targets':list(graph.targets),'blockers':blockers,
        'status':'BLOCKED' if blockers else 'READY','execution_authority':False,
        'current_provider_permission':'NOT_ASSERTED','original_unknown_attempts':unknown,
        'inventory_observability':observable}
    report['local_adapter_capabilities']=capabilities
    report['preflight_digest']=digest(report)
    if blockers:raise NativePublicationPreflightBlocked(report)
    return report
