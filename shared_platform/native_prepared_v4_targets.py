"""Canonical target executors only inside the original protected 403 decision.

No worker, public authority constructor or ordinary adapter fallback is added.
The original native recheck/begin_target/results own execution. The separate
native mutation ledger must reserve durably and persist UNKNOWN before I/O.
"""
from copy import deepcopy
import json
import hashlib
import os
import re
from pathlib import Path

from shared_platform.final_review_server_admission import ApprovalBlocked
from domains.channel_operations.release_executor import AdapterExecutionResult, AdapterRegistration
from domains.channel_operations.omnichannel_orchestrator import ADAPTER_NAMES

OFFER='4039931504'
TASK='TASK-20261006-001'
ORIGINAL_HEAD='e090b96d331ae9608d949ec479327984cb98770a'
PREPARED='r1-prepared:8cb36d2b818d95e27531aba2462bd39905068bd9695519c29676455fc452b922'
TARGETS=('tiktok:LH_PH','tiktok:LH_MY','tiktok:LH_TH','tiktok:LH_VN',
    'tiktok:HB_PH','tiktok:HB_MY','tiktok:HB_TH','tiktok:HB_VN','tiktok:MX','tiktok:GB',
    'shopee:PH','shopee:MY','shopee:TH','shopee:VN','ozon:RU')

def _deny(code):raise ApprovalBlocked(code)

def _file_identity(*,run_id,plan_id,decision_id,snapshot_digest):
    """Deterministic Windows filename aliases, never a new business run.

    PublicationPlatformRequest ids name SDK checkpoint files only. Native
    ReleaseStore claims and the durable ledger retain the original raw ids.
    """
    if (not re.fullmatch(r'release-run:[0-9a-f]{24}',run_id)
            or not plan_id or not decision_id
            or not re.fullmatch(r'sha256:[0-9a-f]{64}',snapshot_digest)):
        _deny('NATIVE_V4_FILE_MAPPING_IDENTITY_INVALID')
    body={'schema_version':'native-final-checkpoint-file-identity/v1','release_run_id':run_id,
        'plan_id':plan_id,'decision_id':decision_id,'execution_snapshot_digest':snapshot_digest}
    digest=hashlib.sha256(json.dumps(body,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    return {**body,'file_run_id':'native-files-'+digest,'file_report_id':'native-report-'+digest}

def _persist_file_identity(root,mapping):
    path=Path(root)/'native-final-file-identities'/(mapping['file_run_id']+'.json')
    path.parent.mkdir(parents=True,exist_ok=True)
    raw=(json.dumps(mapping,sort_keys=True,separators=(',',':'))+'\n').encode()
    try:
        with path.open('xb') as handle:handle.write(raw);handle.flush();os.fsync(handle.fileno())
    except FileExistsError:
        if path.read_bytes()!=raw:_deny('NATIVE_V4_FILE_MAPPING_CHANGED')
    return {'path':str(path),'sha256':hashlib.sha256(raw).hexdigest()}


def provider_request_for_readonly_recovery(active, plan, target, snapshot):
    """Reconstruct the exact original SDK file scope without writes or budgets.

    This is an identity encoder only. The caller must validate the complete
    current native decision/new-task source and original failure before using
    any retained provider response or official reader.
    """
    from shared_platform.native_sole_final_execution import _NativeExecution, _ACTIVE
    from shared_platform.product_publication_runner import PublicationPlatformRequest
    if (type(active) is not _NativeExecution or _ACTIVE.get() is not active
            or active.plan_id != plan['plan_id'] or target['target_label'] not in plan['targets']
            or target['run_id'] != 'release-run:' + plan['payload_digest'][:24]):
        _deny('NATIVE_PROVIDER_RECOVERY_ORIGINAL_SCOPE_REQUIRED')
    mapping = _file_identity(run_id=target['run_id'], plan_id=plan['plan_id'],
        decision_id=active.decision_id, snapshot_digest=snapshot['snapshot_digest'])
    return PublicationPlatformRequest(run_id=mapping['file_run_id'], report_id=mapping['file_report_id'],
        platform=target['target_label'].split(':', 1)[0].upper(), target_labels=(target['target_label'],),
        snapshot=deepcopy(snapshot), checkpoint_root=active.runtime.supervisor.root)

def _frozen_candidate_matches(snapshot,candidate,*,receipt_digest,graph_digest):
    from shared_platform.publication_autopilot import _business_snapshot_digest
    return (snapshot['snapshot_digest']==receipt_digest
        and tuple(candidate['target_labels'])==TARGETS
        and candidate['candidate_digest']==graph_digest
        and candidate['snapshot_digest']==_business_snapshot_digest(snapshot))

def _scope(active,plan):
    from shared_platform.native_sole_final_execution import _ACTIVE,_NativeExecution
    from shared_platform.original_prepared_continuation import OriginalPreparedTaskContinuation
    if type(active) is not _NativeExecution or _ACTIVE.get() is not active:
        _deny('NATIVE_V4_PROTECTED_EXECUTION_REQUIRED')
    original=getattr(active.runtime._operations,'original_continuation',None)
    if (type(original) is not OriginalPreparedTaskContinuation
            or original.pins.offer_id!=OFFER or original.pins.task_id!=TASK
            or original.pins.prepared_reference!=PREPARED
            or original.original_profile.version!=ORIGINAL_HEAD
            or original.engine is not active.runtime._operations.engine
            or original.release_store.path.resolve()!=active.store.path.resolve()
            or plan.get('product_id')!=OFFER or tuple(plan.get('targets',()))!=TARGETS):
        _deny('NATIVE_V4_EXACT_ORIGINAL_CONTINUATION_REQUIRED')
    original.read_documents() # Original concrete six-file R1/R2 check, not a marker.
    return original

def registry_for_original(active,plan,legacy):
    """Choose exact scoped production adapters; other products remain ordinary."""
    if plan.get('product_id')!=OFFER:return legacy
    _scope(active,plan)
    result=dict(legacy)
    for platform in ('tiktok','shopee','ozon'):
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
    if context['store'].path.resolve()!=active.store.path.resolve():
        _deny('NATIVE_V4_ORIGINAL_RELEASE_STORE_CHANGED')
    if request.plan_id!=active.plan_id or request.target_label not in TARGETS:
        _deny('NATIVE_V4_TARGET_OUTSIDE_PROTECTED_PLAN')
    with active.store._connect_readonly() as db:
        db.execute('BEGIN')
        if not allows_native_run(db,active.plan_id):_deny('NATIVE_V4_CURRENT_DECISION_REQUIRED')
        receipt=active.service._decision(db,active.decision_id,owner_sid=active.owner_sid)
        row=db.execute('SELECT * FROM approved_publication_snapshots WHERE plan_id=?',(active.plan_id,)).fetchone()
        if row is None:_deny('NATIVE_V4_APPROVED_SNAPSHOT_REQUIRED')
        snapshot=_validated_publication_snapshot_row(row,plan=plan)
        graph=read_stored_domain_graph(db,active.plan_id,native_reader=NativeCommonSourceReader(active.store))
        candidate=json.loads(graph.display_bytes)
        if not _frozen_candidate_matches(snapshot,candidate,receipt_digest=receipt['snapshot_digest'],
                graph_digest=graph.candidate_digest):
            _deny('NATIVE_V4_FROZEN_CANDIDATE_CHANGED')
    return plan,snapshot,candidate

def _dependencies(active,platform,ledger,references,observations,candidate):
    """Construct only the requested provider after protected source checks."""
    from shared_platform.product_publication_executors import build_product_publication_platform_executors
    from shared_platform.product_publication_live_dependencies import (
        DurableTikTokV4DraftPreparer,TikTokV4DraftCheckpointStore,
        MiaoshouTikTokV4DraftTransportFactory,OfficialMiaoshouTikTokCategoryResolver,
        build_live_tiktok_dependencies,build_live_shopee_dependencies,build_live_ozon_dependencies,
        OfficialOzonV4Transport,OfficialOzonFridgeMagnetProfileResolver)
    if platform=='TIKTOK':
        from modules.miaoshou.client import post_open
        from shared_platform.native_canonical_provider_edges import draft_factory,tiktok_publisher
        from shared_platform.native_tiktok_exact_readback import NativeTikTokExactStorefrontReadback
        post=ledger.wrap_post(post_open,provider='miaoshou')
        inventory=observations.setdefault('tiktok_prewrite_inventory',{})
        official_evidence=observations.setdefault('tiktok_official',{})
        rows=[row for row in candidate['review_manifest']['targets'] if row['target_label']=='tiktok:GB']
        required=rows[0]['category'].get('technical_attribute_candidates') if len(rows)==1 else None
        if (not isinstance(required,list) or len(required)!=1
                or required[0].get('attribute_id')!='102255' or required[0].get('value_id')!='1000256'
                or required[0].get('value_kind')!='OFFICIAL_SINGLE_OPTION_ENUM'):
            _deny('NATIVE_V4_GB_FROZEN_PRODUCT_ATTRIBUTES_CHANGED')
        resolver=OfficialMiaoshouTikTokCategoryResolver(post=post)
        checkpoint=TikTokV4DraftCheckpointStore(active.runtime.supervisor.root)
        preparer=DurableTikTokV4DraftPreparer(checkpoint_store=checkpoint,category_resolver=resolver,
            transport_factory=draft_factory(post,ledger))
        deps=build_live_tiktok_dependencies(category_resolver=resolver,draft_preparer=preparer,
            publisher=tiktok_publisher(post,ledger,inventory),
            storefront_readback=NativeTikTokExactStorefrontReadback(inventory_bindings=inventory,evidence=official_evidence,
                required_product_attributes={'tiktok:GB':deepcopy(required)}))
        return build_product_publication_platform_executors(platform_scope=(platform,),tiktok=deps)[platform]
    if platform=='OZON':
        from shared_platform.ozon_runtime_credentials import required_pinned_ozon_credentials
        from modules.ozon.client import ozon_post_bound
        pinned=required_pinned_ozon_credentials()
        from shared_platform.native_ozon_existing_media_recovery import require_bound_account
        require_bound_account(ledger,pinned.account_id)
        def account_post(path,body):return ozon_post_bound(path,body,client_id=pinned.account_id,api_key=pinned.api_key)
        post=ledger.wrap_post(account_post,provider='ozon')
        from shared_platform.native_canonical_provider_edges import ozon_transport
        transport=ozon_transport(post,ledger,pinned.account_id,references)
        # Profile, warehouse, import and readback share this same account-bound post.
        deps=build_live_ozon_dependencies(transport=transport,
            official_profile_resolver=OfficialOzonFridgeMagnetProfileResolver(post=post))
        return build_product_publication_platform_executors(platform_scope=(platform,),ozon=deps)[platform]
    # Canonical regional executor uses the same ledger's original before_mutation
    # callback. The shared global preparer must also use this ledger.
    from shared_platform.native_canonical_provider_edges import shopee_dependencies
    deps=shopee_dependencies(ledger,active.runtime.supervisor.root,references)
    from shared_platform.native_shopee_known_zero_recovery import existing_item_executor
    existing=existing_item_executor(active,ledger,references,deps)
    if existing is not None:return existing
    return build_product_publication_platform_executors(platform_scope=(platform,),shopee=deps)[platform]

def _result(platform_result,request,ledger,*,provider_reference=None,durable_seams=False):
    rows=platform_result.get('targets') or []
    if len(rows)!=1 or rows[0].get('target_label')!=request.target_label:
        _deny('NATIVE_V4_RESULT_TARGET_CHANGED')
    row=rows[0];status=row.get('status');proof=row.get('evidence') or {}
    budget=ledger.snapshot()
    own=[r for r in budget['reservations'] if r.get('mutation_target_label')==request.target_label]
    known_zero=durable_seams and not own
    unknown=(not known_zero and (proof.get('outcome_unknown') is True or platform_result.get('external_write_count') is None)
        or any(r.get('outcome')=='UNKNOWN' and (r.get('mutation_target_label')==request.target_label
                or (request.target_label.startswith('shopee:') and r.get('scope')=='shared'))
            for r in budget['reservations']))
    published=(status=='PUBLISHED' and platform_result.get('readback_completed') is True and not unknown)
    accepted=(status=='PROCESSING' and platform_result.get('dispatch_attempted') is True and not unknown)
    external=provider_reference if accepted or published else None
    # A submitted target needs the provider's actual draft/import/item identity,
    # never a local plan id or invented success marker.
    if accepted and not external:accepted=False;unknown=True
    evidence={'schema_version':'native-scoped-v4-target-result/v1','target_label':request.target_label,
        'verified':published,'request_attempted':not known_zero and platform_result.get('dispatch_attempted') is True,
        'outcome_unknown':unknown,'reconciliation_required':unknown,'status':status,
        'canonical_platform_result':deepcopy(platform_result),'mutation_budget':budget,
        'external_writes_performed':[] if known_zero or platform_result.get('external_write_count')==0 else [request.target_label],
        'known_pre_submit_zero':known_zero and status!='PUBLISHED'}
    if accepted:
        evidence['accepted']=True
    return AdapterExecutionResult(published or accepted,published,
        'Canonical target official readback verified' if published else 'Canonical target requires exact official readback/reconciliation',
        external,evidence,accepted)

def _pre_submit_failure(request,error):
    code=str(error)
    if not re.fullmatch(r'[A-Z][A-Z0-9_]{1,120}',code):code='NATIVE_V4_LOCAL_DEPENDENCY_UNAVAILABLE'
    return AdapterExecutionResult(False,False,code,None,{
        'schema_version':'native-scoped-v4-target-result/v1','target_label':request.target_label,
        'verified':False,'status':'FAILED','request_attempted':False,'outcome_unknown':False,
        'known_pre_submit_zero':True,'reconciliation_required':False,'external_writes_performed':[]},False)

def _execute(active,request):
    state={'provider_entered':False}
    try:return _execute_checked(active,request,state)
    except (OSError,ValueError,KeyError,TypeError,RuntimeError) as error:
        ledger=state.get('ledger')
        if not state['provider_entered']:return _pre_submit_failure(request,error)
        if ledger is not None:
            return _result({'targets':[{'target_label':request.target_label,'status':'FAILED','evidence':{}}],
                'dispatch_attempted':True,'readback_completed':False,'external_write_count':None},request,ledger,durable_seams=True)
        raise

def _execute_checked(active,request,state):
    from shared_platform.product_publication_runner import PublicationPlatformRequest
    from shared_platform.native_final_mutation_budget import build_native_budget_ledger
    plan,snapshot,candidate=_approved_inputs(active,request)
    platform=request.channel.upper()
    ledger=build_native_budget_ledger(active,platform=platform,target_label=request.target_label,
        snapshot=snapshot,candidate=candidate)
    if platform=='OZON':
        from shared_platform.native_ozon_existing_media_recovery import for_active as existing_media_for_active
        ledger=existing_media_for_active(active,ledger)
    state['ledger']=ledger
    references=[];observations={}
    execute=_dependencies(active,platform,ledger,references,observations,candidate)
    run=active.store.get_run('release-run:'+plan['payload_digest'][:24])
    if not run:_deny('NATIVE_V4_ORIGINAL_RUN_REQUIRED')
    mapping=_file_identity(run_id=run['run_id'],plan_id=plan['plan_id'],decision_id=active.decision_id,
        snapshot_digest=snapshot['snapshot_digest'])
    mapping_ref=_persist_file_identity(active.runtime.supervisor.root,mapping)
    native_request=PublicationPlatformRequest(run_id=mapping['file_run_id'],report_id=mapping['file_report_id'],
        platform=platform,target_labels=(request.target_label,),snapshot=deepcopy(snapshot),
        release_candidate=deepcopy(candidate),write_budget=deepcopy(candidate['write_budget'][platform]),
        write_budget_ledger=ledger,checkpoint_root=active.runtime.supervisor.root)
    state['provider_entered']=True
    result=execute(native_request)
    reference=None
    if platform=='TIKTOK':
        from shared_platform.product_publication_live_dependencies import TikTokV4DraftCheckpointStore
        events=TikTokV4DraftCheckpointStore(active.runtime.supervisor.root).load(native_request)['events']
        identities={(e['detail_id'],e['shop_id']) for e in events
            if e['target_label']==request.target_label and e['outcome']=='ACCEPTED'
            and e['detail_id'] is not None and e['shop_id'] is not None}
        if len(identities)==1:
            detail,shop=next(iter(identities));reference='miaoshou:tiktok:'+shop+':'+detail
    elif references:
        reference=','.join(dict.fromkeys(references))
    adapter_result=_result(result,request,ledger,provider_reference=reference,durable_seams=True)
    from dataclasses import replace
    return replace(adapter_result,readback_evidence={**adapter_result.readback_evidence,
        'provider_readonly_observations':deepcopy(observations),
        'checkpoint_file_identity':mapping,'checkpoint_file_identity_ref':mapping_ref})
