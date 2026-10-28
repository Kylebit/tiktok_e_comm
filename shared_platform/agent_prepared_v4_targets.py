"""Canonical v4 execution for one startup-owned signed Agent adoption.

The old retained-product route grants no authority here. The current task,
stored candidate, same-owner sole decision and durable mutation ledger are
rechecked before the exact target is dispatched. Readback supplies catalog rows.
"""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from domains.channel_operations.release_executor import AdapterRegistration
from domains.channel_operations.omnichannel_orchestrator import ADAPTER_NAMES
from shared_platform.final_review_server_admission import ApprovalBlocked

def _scope(active,plan):
    from shared_platform.native_sole_final_execution import _ACTIVE,_NativeExecution
    from shared_platform.agent_prepared_common_service import AgentPreparedCommonService
    if type(active) is not _NativeExecution or _ACTIVE.get() is not active:
        raise ApprovalBlocked('ADOPTION_PROTECTED_V4_SCOPE_REQUIRED')
    bound=getattr(active.runtime._operations,'agent_prepared_common',None)
    if type(bound) is not AgentPreparedCommonService:
        raise ApprovalBlocked('ADOPTION_PROTECTED_V4_SCOPE_REQUIRED')
    task,record=bound._current()
    from shared_platform.prepared_target_consumer import effective_marketplace_targets
    effective=effective_marketplace_targets(record['offer_id'],record['targets'])
    if (bound.final is not active.runtime or bound.store is not active.store
        or record['owner_sid']!=active.owner_sid or record['offer_id']!=plan.get('product_id')
        or sorted(plan.get('targets') or [])!=sorted(effective)):
        raise ApprovalBlocked('ADOPTION_PROTECTED_V4_SCOPE_CHANGED')
    return bound,task,record

def registry_for_adopted(active,plan,legacy):
    _scope(active,plan)
    result=dict(legacy)
    for platform in {label.split(':')[0] for label in plan['targets']}:
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
    context=_validated_context(request);plan=context['plan'];_scope(active,plan)
    if context['store'].path.resolve()!=active.store.path.resolve() or request.plan_id!=active.plan_id or request.target_label not in plan['targets']:
        raise ApprovalBlocked('ADOPTION_V4_REQUEST_OUTSIDE_FROZEN_PLAN')
    with active.store._connect_readonly() as db:
        db.execute('BEGIN')
        if not allows_native_run(db,active.plan_id):raise ApprovalBlocked('ADOPTION_CURRENT_NATIVE_DECISION_REQUIRED')
        receipt=active.service._decision(db,active.decision_id,owner_sid=active.owner_sid)
        row=db.execute('SELECT * FROM approved_publication_snapshots WHERE plan_id=?',(active.plan_id,)).fetchone()
        if row is None:raise ApprovalBlocked('ADOPTION_STORED_V4_SNAPSHOT_REQUIRED')
        snapshot=_validated_publication_snapshot_row(row,plan=plan)
        graph=read_stored_domain_graph(db,active.plan_id,native_reader=NativeCommonSourceReader(active.store))
        candidate=json.loads(graph.display_bytes)
        if (snapshot['snapshot_digest']!=receipt['snapshot_digest'] or candidate['candidate_digest']!=graph.candidate_digest
            or candidate['snapshot_digest']!=_business_snapshot_digest(snapshot)
            or candidate['target_labels']!=plan['targets']):raise ApprovalBlocked('ADOPTION_FROZEN_CANDIDATE_CHANGED')
    return plan,snapshot,candidate

def _catalog_sink(active,plan,snapshot):
    bound,task,record=_scope(active,plan)
    from modules.sourcing import new_product_workbench as workbench
    from shared_platform.publication_canonical_aliases import create_frozen_alias_authority
    from shared_platform.catalog_publication_sync import CatalogPublicationSync
    profile=bound.profile;offer=record['offer_id']
    if bound.catalog_path is None:raise ApprovalBlocked('ADOPTION_CATALOG_WRITER_BINDING_REQUIRED')
    authority=create_frozen_alias_authority(offer_id=offer,
        round1_snapshot=record['facts']['preparation_source']['round1_snapshot'],
        state=workbench.load_state(offer,state_dir=profile.root/'data/new_product_workbench'),
        report_directory=profile.root/'reports/product-preparation'/offer,
        expected_binding_sha256=record['facts']['source_identity_binding_sha256'],publication_snapshot=snapshot)
    return CatalogPublicationSync(bound.catalog_path,profile.data_root/'catalog-sync',canonical_authority=authority)

def _dependencies(active,platform,ledger,references,observations):
    from shared_platform.product_publication_executors import build_product_publication_platform_executors
    from shared_platform.product_publication_live_dependencies import (
        DurableTikTokV4DraftPreparer,TikTokV4DraftCheckpointStore,MiaoshouTikTokV4DraftTransportFactory,
        OfficialMiaoshouTikTokCategoryResolver,build_live_tiktok_dependencies,build_live_shopee_dependencies,
        build_live_ozon_dependencies,OfficialOzonFridgeMagnetProfileResolver)
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
        from shared_platform.native_tiktok_locale_draft_correction import bind_preparer,bind_publisher
        preparer=bind_preparer(preparer,ledger,resolver)
        deps=build_live_tiktok_dependencies(category_resolver=resolver,draft_preparer=preparer,
            publisher=bind_publisher(edges.tiktok_publisher(post,ledger,inventory),ledger),
            storefront_readback=NativeTikTokExactStorefrontReadback(inventory_bindings=inventory,evidence=official))
        return build_product_publication_platform_executors(platform_scope=(platform,),tiktok=deps)[platform]
    if platform=='OZON':
        from shared_platform.ozon_runtime_credentials import required_pinned_ozon_credentials
        from modules.ozon.client import ozon_post_bound
        pinned=required_pinned_ozon_credentials()
        from shared_platform.native_ozon_three_card_media_recovery import require_bound_account
        require_bound_account(ledger,pinned.account_id)
        def account_post(path,body):return ozon_post_bound(path,body,client_id=pinned.account_id,api_key=pinned.api_key)
        post=ledger.wrap_post(account_post,provider='ozon')
        deps=build_live_ozon_dependencies(transport=edges.ozon_transport(post,ledger,pinned.account_id,references),
            official_profile_resolver=OfficialOzonFridgeMagnetProfileResolver(post=post))
        return build_product_publication_platform_executors(platform_scope=(platform,),ozon=deps)[platform]
    deps=edges.shopee_dependencies(ledger,active.runtime.supervisor.root,references)
    from shared_platform.native_shopee_regional_continuation import bind_dependencies
    deps=bind_dependencies(deps,ledger)
    return build_product_publication_platform_executors(platform_scope=(platform,),shopee=deps)[platform]

def _execute(active,request):
    # Only pure filename/result encoders are shared with the historical route;
    # none of its origin validators, allowances or recovery exceptions run.
    from shared_platform.native_prepared_v4_targets import _file_identity,_persist_file_identity,_result,_pre_submit_failure
    from shared_platform.product_publication_runner import PublicationPlatformRequest
    from shared_platform.native_final_mutation_budget import build_native_budget_ledger
    entered=False;ledger=None
    try:
        plan,snapshot,candidate=_approved_inputs(active,request)
        platform=request.channel.upper()
        ledger=build_native_budget_ledger(active,platform=platform,target_label=request.target_label,snapshot=snapshot,candidate=candidate)
        if platform=='TIKTOK':
            from shared_platform.native_tiktok_locale_draft_correction import for_active
            ledger=for_active(active,ledger)
        if platform=='SHOPEE':
            from shared_platform.native_shopee_regional_continuation import for_active
            ledger=for_active(active,ledger)
        if platform=='OZON':
            from shared_platform.native_ozon_three_card_media_recovery import for_active
            ledger=for_active(active,ledger)
        references=[];observations={}
        execute=_dependencies(active,platform,ledger,references,observations)
        if platform=='OZON':
            from shared_platform.native_ozon_three_card_media_recovery import bind_executor
            execute=bind_executor(active,ledger,execute)
        run=active.store.get_run('release-run:'+plan['payload_digest'][:24])
        if not run:raise ApprovalBlocked('ADOPTION_NATIVE_RELEASE_RUN_REQUIRED')
        mapping=_file_identity(run_id=run['run_id'],plan_id=plan['plan_id'],decision_id=active.decision_id,snapshot_digest=snapshot['snapshot_digest'])
        reference=_persist_file_identity(active.runtime.supervisor.root,mapping)
        sink=_catalog_sink(active,plan,snapshot)
        native_request=PublicationPlatformRequest(run_id=mapping['file_run_id'],report_id=mapping['file_report_id'],
            platform=platform,target_labels=(request.target_label,),snapshot=deepcopy(snapshot),
            release_candidate=deepcopy(candidate),write_budget=deepcopy(candidate['write_budget'][platform]),
            write_budget_ledger=ledger,checkpoint_root=active.runtime.supervisor.root,catalog_sink=sink)
        sink.begin(native_request)
        entered=True;result=execute(native_request)
        # Canonical executors call capture_readback with actual complete official
        # rows; the typed sink owns projection/recovery, not a result hash here.
        provider_reference=','.join(dict.fromkeys(references)) or None
        if platform=='TIKTOK':
            from shared_platform.product_publication_live_dependencies import TikTokV4DraftCheckpointStore
            events=TikTokV4DraftCheckpointStore(active.runtime.supervisor.root).load(native_request)['events']
            identities={(e['detail_id'],e['shop_id']) for e in events if e['target_label']==request.target_label
                and e['outcome']=='ACCEPTED' and e['detail_id'] is not None and e['shop_id'] is not None}
            if len(identities)==1:
                detail,shop=next(iter(identities));provider_reference='miaoshou:tiktok:'+shop+':'+detail
        value=_result(result,request,ledger,provider_reference=provider_reference,durable_seams=True)
        value=replace(value,readback_evidence={**value.readback_evidence,'provider_readonly_observations':deepcopy(observations),
            'checkpoint_file_identity':mapping,'checkpoint_file_identity_ref':reference,
            'catalog_projection_failures':deepcopy(sink.failures)})
        if platform=='OZON':
            from shared_platform.native_ozon_three_card_media_recovery import retain_original_reference
            value=retain_original_reference(active,value,ledger)
        return value
    except (OSError,ValueError,KeyError,TypeError,RuntimeError) as error:
        if not entered:
            value=_pre_submit_failure(request,error)
            if request.channel.upper()=='OZON':
                from shared_platform.native_ozon_three_card_media_recovery import retain_original_reference
                value=retain_original_reference(active,value,ledger)
            return value
        if ledger is None:raise
        value=_result({'targets':[{'target_label':request.target_label,'status':'FAILED','evidence':{}}],
            'dispatch_attempted':True,'readback_completed':False,'external_write_count':None},request,ledger,durable_seams=True)
        if request.channel.upper()=='OZON':
            from shared_platform.native_ozon_three_card_media_recovery import retain_original_reference
            value=retain_original_reference(active,value,ledger)
        return value
