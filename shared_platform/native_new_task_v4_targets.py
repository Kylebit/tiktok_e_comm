"""Canonical v4 adapters for a genuine current explicit-new publication task.

This is not an adoption/repair grant. The existing task producer, immutable
R1/R2, COMMON graph, same-owner native decision and durable mutation ledger
remain authorities. No historical Offer, SKU, target count or item is pinned.
"""
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
from domains.channel_operations.release_executor import AdapterRegistration
from domains.channel_operations.omnichannel_orchestrator import ADAPTER_NAMES
from shared_platform.final_review_server_admission import ApprovalBlocked


def local_execution_capabilities(snapshot,candidate):
    """Run the installed adapters' pure consumers, never READY flags or APIs.

    Provider permission/current ID availability is deliberately not asserted by
    local shape checks; execution still uses exact official rechecks.
    """
    from modules.miaoshou.tiktok_v4_drafts import _draft_payload
    from shared_platform.product_publication_live_dependencies import (
        _shopee_expected_models,OfficialOzonFridgeMagnetProfileResolver)
    value=deepcopy(snapshot)
    value.setdefault('snapshot_digest',value.get('preview_digest'))
    labels=[r['target_label'] for r in value['publication_targets']]
    if candidate.get('target_labels')!=labels:raise ValueError('LOCAL_CAPABILITY_CANDIDATE_TARGETS_CHANGED')
    rows=[]
    for target in value['publication_targets']:
        label=target['target_label'];platform=label.split(':',1)[0];blockers=[]
        try:
            if platform=='tiktok':
                category=value['categories_by_target'][label]['category']
                draft=_draft_payload(value,target=target,category=category)
                if len(draft['skus'])!=len(value['skus']):raise ValueError('model coverage')
            elif platform=='shopee':
                _shopee_expected_models(value,[t for t in labels if t.startswith('shopee:')])
            elif platform=='ozon':
                OfficialOzonFridgeMagnetProfileResolver._profile_ids(value)
            else:raise ValueError('unknown adapter')
        except (ValueError,KeyError,TypeError) as error:
            blockers.append({'code':'LOCAL_ADAPTER_FROZEN_INPUT_UNSUPPORTED','error_type':type(error).__name__,
                'detail':str(error)[:240]})
        rows.append({'target_label':label,'local_adapter_supported':not blockers,'blockers':blockers,
                     'current_provider_permission':'NOT_ASSERTED','official_readback_required':True})
    return {'schema_version':'native-local-publication-capabilities/v1','targets':rows,'execution_authority':False}


def scope(active, plan):
    from shared_platform.native_sole_final_execution import _ACTIVE, _NativeExecution
    if type(active) is not _NativeExecution or _ACTIVE.get() is not active:
        raise ApprovalBlocked('NEW_TASK_V4_PROTECTED_EXECUTION_REQUIRED')
    return preparation_scope(active.runtime,plan)


def preparation_scope(runtime,plan):
    """Read the same genuine explicit task/R1/R2 before final review; no grant."""
    from shared_platform.native_sole_final_service import NativeSoleFinalService
    from shared_platform.native_task_preparation import ExplicitNewTaskWorker
    from shared_platform import workbench_publication_native as native
    if type(runtime) is not NativeSoleFinalService:raise ApprovalBlocked('NEW_TASK_V4_RUNTIME_REQUIRED')
    operations=runtime._operations
    if (operations is None or getattr(operations,'original_continuation',None) is not None
            or getattr(operations,'agent_prepared_common',None) is not None):
        raise ApprovalBlocked('NEW_TASK_V4_NOT_A_HISTORICAL_CONTINUATION')
    worker=getattr(operations,'new_task_worker',None)
    if (type(worker) is not ExplicitNewTaskWorker or worker.engine is not operations.engine
            or worker.profile is not operations.profile):
        raise ApprovalBlocked('NEW_TASK_V4_EXPLICIT_WORKER_REQUIRED')
    profile=operations.profile
    release={'code_version':profile.version,'environment':profile.environment,
             'manifest_digest':profile.manifest_digest}
    if operations.engine.release!=release or profile.environment!='stable':
        raise ApprovalBlocked('NEW_TASK_V4_RELEASE_CHANGED')
    # Resolve this plan's exact immutable preparation owner. Do not sweep all
    # same-Offer history, expire leases or enter engine.get's write transaction
    # once per target. The trusted explicit-POST grant is checked in that same
    # read-only engine snapshot; a caller task_id cannot select an owner.
    binding=plan['payload'].get('r3_marketplace_binding') or {}
    common=runtime.store.get_plan(binding.get('common_plan_id'))
    source=((common or {}).get('payload',{}).get('r3_stage_binding',{}).get('native_preparation_source') or {})
    reference=source.get('prepared_reference')
    if not reference or source.get('offer_id')!=plan.get('product_id'):
        raise ApprovalBlocked('NEW_TASK_V4_PREPARATION_OWNER_REFERENCE_REQUIRED')
    engine=operations.engine
    with engine.readonly_snapshot() as db:
        rows=db.execute("SELECT DISTINCT e.task_id FROM workbench_events e JOIN workbench_execution x USING(task_id) "
            "WHERE e.event_type='checkpoint_saved' AND x.template='publication' "
            "AND json_extract(x.scope_json,'$.offer_id')=? AND "
            "(json_extract(e.detail_json,'$.checkpoint.native_preparation.prepared_reference')=? OR "
            "json_extract(e.detail_json,'$.checkpoint.native_r1.prepared_reference')=?) LIMIT 2",
            (plan['product_id'],reference,reference)).fetchall()
        if len(rows)!=1:raise ApprovalBlocked('NEW_TASK_V4_UNIQUE_ORIGINAL_TASK_REQUIRED')
        task=engine._project(db,engine._row(db,rows[0]['task_id']))
        grants=db.execute("SELECT g.detail_json,e.request_digest,t.source_key FROM workbench_events g "
            "JOIN workbench_execution e USING(task_id) JOIN workbench_tasks t USING(task_id) "
            "WHERE g.task_id=? AND g.event_type='explicit_new_post_preparation'",(task['task_id'],)).fetchall()
        if len(grants)!=1:raise ApprovalBlocked('NEW_TASK_V4_EXPLICIT_ORIGIN_REQUIRED')
        grant=json.loads(grants[0]['detail_json'])
        if (set(grant)!={'request_digest','source_key','scope','release'} or grant['release']!=release
                or grant['request_digest']!=grants[0]['request_digest'] or grant['source_key']!=grants[0]['source_key']
                or grant['scope'].get('offer_id')!=task['scope']['offer_id']
                or grant['scope'].get('shops')!=task['scope']['shops']):
            raise ApprovalBlocked('NEW_TASK_V4_EXPLICIT_ORIGIN_CHANGED')
    if (not worker.owns(task['task_id']) or task.get('version')!=release
            or task.get('template')!='publication'
            or task.get('execution_state') in {'cancelled','failed'}):
        raise ApprovalBlocked('NEW_TASK_V4_TASK_OR_VERSION_CHANGED')
    frozen=native.read_frozen(task,profile)
    images=native.read_images(task,profile)
    targets=[t for t in task['scope']['shops'] if t!='miaoshou:COMMON']
    if (not frozen or frozen['offer_id']!=plan['product_id']
            or sorted(frozen['targets'])!=sorted(task['scope']['shops'])
            or sorted(plan.get('targets') or [])!=sorted(targets)
            or images['native_r1']!=frozen
            or images['native_r2']!=binding.get('r2_identity')
            or binding.get('documents',{}).get('round1_snapshot',{}).get('snapshot_digest')!=frozen['snapshot_digest']):
        raise ApprovalBlocked('NEW_TASK_V4_FROZEN_PREDECESSORS_CHANGED')
    return operations,task,frozen,images


def registry_for_new_task(active,plan,legacy):
    scope(active,plan)
    result=dict(legacy)
    for platform in {t.split(':',1)[0] for t in plan['targets']}:
        name=ADAPTER_NAMES[platform]
        result[name]=AdapterRegistration(name,lambda request:_execute(active,request),True,True,True,True,
                                         automatic_first_attempt_mode='ENABLED')
    return result


def _approved_inputs(active,request):
    from modules.products.release_adapters import _validated_context
    from shared_platform.native_sole_final_execution import allows_native_run
    from shared_platform.release_store import _validated_publication_snapshot_row
    from shared_platform.r3_frozen_review_producer import read_stored_domain_graph
    from shared_platform.r3_common_source_facts import NativeCommonSourceReader
    from shared_platform.publication_autopilot import _business_snapshot_digest
    context=_validated_context(request);plan=context['plan'];scope(active,plan)
    if (context['store'].path.resolve()!=active.store.path.resolve()
            or request.plan_id!=active.plan_id or request.target_label not in plan['targets']):
        raise ApprovalBlocked('NEW_TASK_V4_REQUEST_OUTSIDE_FROZEN_PLAN')
    with active.store._connect_readonly() as db:
        db.execute('BEGIN')
        if not allows_native_run(db,active.plan_id):raise ApprovalBlocked('NEW_TASK_V4_CURRENT_DECISION_REQUIRED')
        receipt=active.service._decision(db,active.decision_id,owner_sid=active.owner_sid)
        row=db.execute('SELECT * FROM approved_publication_snapshots WHERE plan_id=?',(active.plan_id,)).fetchone()
        if row is None:raise ApprovalBlocked('NEW_TASK_V4_STORED_SNAPSHOT_REQUIRED')
        snapshot=_validated_publication_snapshot_row(row,plan=plan)
        graph=read_stored_domain_graph(db,active.plan_id,native_reader=NativeCommonSourceReader(active.store))
        candidate=json.loads(graph.display_bytes)
        if (snapshot['snapshot_digest']!=receipt['snapshot_digest']
                or candidate['candidate_digest']!=graph.candidate_digest
                or candidate['snapshot_digest']!=_business_snapshot_digest(snapshot)
                or candidate['target_labels']!=plan['targets']):
            raise ApprovalBlocked('NEW_TASK_V4_CURRENT_CANDIDATE_CHANGED')
    return plan,snapshot,candidate


def _dependencies(active,platform,ledger,references,observations):
    from shared_platform.product_publication_executors import build_product_publication_platform_executors
    from shared_platform.product_publication_live_dependencies import (
        DurableTikTokV4DraftPreparer,TikTokV4DraftCheckpointStore,OfficialMiaoshouTikTokCategoryResolver,
        build_live_tiktok_dependencies,build_live_ozon_dependencies,OfficialOzonFridgeMagnetProfileResolver)
    from shared_platform import native_canonical_provider_edges as edges
    if platform=='TIKTOK':
        from modules.miaoshou.client import post_open
        from shared_platform.native_tiktok_exact_readback import NativeTikTokExactStorefrontReadback
        post=ledger.wrap_post(post_open,provider='miaoshou')
        inventory=observations.setdefault('tiktok_prewrite_inventory',{})
        official=observations.setdefault('tiktok_official',{})
        resolver=OfficialMiaoshouTikTokCategoryResolver(post=post)
        preparer=DurableTikTokV4DraftPreparer(checkpoint_store=TikTokV4DraftCheckpointStore(active.runtime.supervisor.root),
            category_resolver=resolver,transport_factory=edges.draft_factory(post,ledger))
        deps=build_live_tiktok_dependencies(category_resolver=resolver,draft_preparer=preparer,
            publisher=edges.tiktok_publisher(post,ledger,inventory),
            storefront_readback=NativeTikTokExactStorefrontReadback(inventory_bindings=inventory,evidence=official))
    elif platform=='SHOPEE':
        deps=edges.shopee_dependencies(ledger,active.runtime.supervisor.root,references)
    elif platform=='OZON':
        from shared_platform.ozon_runtime_credentials import required_pinned_ozon_credentials
        from modules.ozon.client import ozon_post_bound
        pinned=required_pinned_ozon_credentials()
        def account_post(path,body):return ozon_post_bound(path,body,client_id=pinned.account_id,api_key=pinned.api_key)
        post=ledger.wrap_post(account_post,provider='ozon')
        deps=build_live_ozon_dependencies(transport=edges.ozon_transport(post,ledger,pinned.account_id,references),
            official_profile_resolver=OfficialOzonFridgeMagnetProfileResolver(post=post))
    else:raise ApprovalBlocked('NEW_TASK_V4_PLATFORM_UNSUPPORTED')
    return build_product_publication_platform_executors(platform_scope=(platform,),**{platform.lower():deps})[platform]


def _catalog_sink(active,plan,snapshot):
    """Use the startup-selected catalog, never a source-tree/cwd default."""
    from core import db as catalog
    from shared_platform.catalog_publication_sync import CatalogPublicationSync
    profile=active.runtime._operations.profile
    bound=os.environ.get('ORBIT_CATALOG_DATABASE')
    if (not bound or not Path(bound).is_absolute() or not Path(bound).is_file()
            or Path(bound).resolve()!=catalog.db_path().resolve()):
        raise ApprovalBlocked('NEW_TASK_V4_CATALOG_STARTUP_BINDING_REQUIRED')
    # Ordinary Model identities are explicit four-digit canonical SKUs. A new
    # alias requires a separately source-bound authority, never suffix guessing.
    import re
    if any(not re.fullmatch(r'[0-9]{4}',r['model_sku']) for r in snapshot['skus']):
        raise ApprovalBlocked('NEW_TASK_V4_CANONICAL_ALIAS_AUTHORITY_REQUIRED')
    return CatalogPublicationSync(Path(bound).resolve(),profile.data_root/'catalog-sync')


def _execute(active,request):
    from shared_platform.native_prepared_v4_targets import _file_identity,_persist_file_identity,_result,_pre_submit_failure
    from shared_platform.product_publication_runner import PublicationPlatformRequest
    from shared_platform.native_final_mutation_budget import build_native_budget_ledger
    entered=False;ledger=None
    try:
        plan,snapshot,candidate=_approved_inputs(active,request)
        platform=request.channel.upper()
        ledger=build_native_budget_ledger(active,platform=platform,target_label=request.target_label,
                                          snapshot=snapshot,candidate=candidate)
        references=[];observations={}
        execute=_dependencies(active,platform,ledger,references,observations)
        run=active.store.get_run('release-run:'+plan['payload_digest'][:24])
        if run is None:raise ApprovalBlocked('NEW_TASK_V4_ORIGINAL_RUN_REQUIRED')
        mapping=_file_identity(run_id=run['run_id'],plan_id=plan['plan_id'],decision_id=active.decision_id,
                               snapshot_digest=snapshot['snapshot_digest'])
        reference=_persist_file_identity(active.runtime.supervisor.root,mapping)
        sink=_catalog_sink(active,plan,snapshot)
        native_request=PublicationPlatformRequest(run_id=mapping['file_run_id'],report_id=mapping['file_report_id'],
            platform=platform,target_labels=(request.target_label,),snapshot=deepcopy(snapshot),
            release_candidate=deepcopy(candidate),write_budget=deepcopy(candidate['write_budget'][platform]),
            write_budget_ledger=ledger,checkpoint_root=active.runtime.supervisor.root,catalog_sink=sink)
        entered=True;result=execute(native_request)
        from shared_platform.native_provider_continuation import continue_original_result
        value=continue_original_result(active,request,native_request,result,ledger)
        if value is None:
            value=_result(result,request,ledger,provider_reference=','.join(dict.fromkeys(references)) or None,durable_seams=True)
        return replace(value,readback_evidence={**value.readback_evidence,
            'provider_readonly_observations':observations,'checkpoint_file_identity':mapping,
            'checkpoint_file_identity_ref':reference})
    except (OSError,ValueError,KeyError,TypeError,RuntimeError) as error:
        if not entered:return _pre_submit_failure(request,error)
        if ledger is None:raise
        return _result({'targets':[{'target_label':request.target_label,'status':'FAILED','evidence':{}}],
            'dispatch_attempted':True,'readback_completed':False,'external_write_count':None},request,ledger,durable_seams=True)
