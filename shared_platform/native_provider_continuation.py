"""Continue only unexecuted approved steps after an original retained CREATE.

Runs within the original native attempt, decision, platform lock and durable
ledger. It never calls the global resolver or create_publish_task. A lost wire,
ambiguous identity, changed source, repeated operation or exhausted ceiling is
a HOLD. The original UNKNOWN reservation is never changed or released; only
its separately append-only confirmed outcome may be recorded after exact READ.
"""
from copy import deepcopy
import re

from domains.channel_operations.release_executor import AdapterExecutionResult
from shared_platform.final_review_server_admission import ApprovalBlocked


def _need(value, code):
    if not value:raise ApprovalBlocked('NATIVE_ORIGINAL_CONTINUATION_'+code)


def _plan_actions(material):
    checks=material['checks'];actions=[]
    if not checks['exact_ordered_image_urls'] or not checks['image_identities_complete']:
        actions.extend(('UPLOAD_APPROVED_IMAGES','UPDATE_APPROVED_IMAGES'))
    if not checks['copy_exact']:actions.append('UPDATE_APPROVED_COPY')
    if ('UPLOAD_APPROVED_IMAGES' in actions or 'UPDATE_APPROVED_COPY' in actions
            or not checks['description_images_exact'] or not checks['description_type_extended']):
        actions.append('UPDATE_APPROVED_DESCRIPTION')
    if not checks['normal_status_exact']:actions.append('LIST_ORIGINAL_ITEM')
    return actions


_OPERATIONS={'UPLOAD_APPROVED_IMAGES':'upload_regional_image','UPDATE_APPROVED_IMAGES':'update_regional_images',
    'UPDATE_APPROVED_COPY':'update_regional_copy','UPDATE_APPROVED_DESCRIPTION':'update_description_media',
    'LIST_ORIGINAL_ITEM':'list_regional_item'}


def continue_original_result(active, adapter_request, native_request, result, ledger):
    """Return an actual verified adapter result, or None to retain UNKNOWN.

    This is not a FAILED-target retry API. The original native consumer may
    call it only while its initial claimed target is still RUNNING.
    """
    if (native_request.platform!='SHOPEE' or result.get('readback_completed') is not True
            or result.get('external_write_count') is not None):return None
    from shared_platform.native_sole_final_execution import _ACTIVE,_NativeExecution
    from shared_platform.native_new_task_v4_targets import _approved_inputs
    from shared_platform import native_sole_final_readback as readback
    from shared_platform.publication_provider_journal import retained_provider_executor
    from shared_platform.shopee_publication_checkpoint import ShopeePublicationCheckpointStore
    try:
        _need(type(active) is _NativeExecution and _ACTIVE.get() is active,'CURRENT_NATIVE_SCOPE_REQUIRED')
        plan,snapshot,candidate=_approved_inputs(active,adapter_request)
        _need(snapshot==native_request.snapshot,'FROZEN_SNAPSHOT_CHANGED')
        run=active.store.get_run('release-run:'+plan['payload_digest'][:24])
        target=next(t for t in run['targets'] if t['target_label']==adapter_request.target_label)
        _need(target['status']=='RUNNING' and target['attempts']==1,'ORIGINAL_RUNNING_ATTEMPT_REQUIRED')
        budget=ledger.snapshot();own=[r for r in budget['reservations'] if r['mutation_target_label']==target['target_label']]
        _need(not any(r['scope']=='shared' and r['outcome']=='UNKNOWN' for r in budget['reservations']),
              'SHARED_UNKNOWN_REQUIRES_READONLY_RECONCILIATION')
        unknown=[r for r in own if r['outcome']=='UNKNOWN']
        _need(len(unknown)==1 and unknown[0]['operation']=='create_publish_task','EXACT_ORIGINAL_CREATE_UNKNOWN_REQUIRED')
        facts={'plan':plan,'snapshot':snapshot,'target':target,'ledger':budget['reservations'],
               'failure':{'evidence_digest':None}}
        source=readback._provider_original_task(active,facts)
        responses=readback._provider_official_reads(active,source)
        material=readback._provider_verified_evidence(facts,source,responses,material_only=True)
        identity_checks={'item_identity_exact','global_linkage_exact','model_sku_set_exact','model_ids_present',
            'model_tiers_exact','model_prices_exact','item_sku_exact','applicable_logistics_enabled',
            'frozen_category_exact','parcel_exact','frozen_stock_exact'}
        _need(all(material['checks'][name] is True for name in identity_checks),'ORIGINAL_ITEM_IDENTITY_OR_FACTS_MISMATCH')
        pending=ledger._pending.get('create_publish_task')
        _need(pending is not None and pending['sequence']==unknown[0]['sequence'],
              'ORIGINAL_PENDING_RESERVATION_REQUIRED')
        with ledger._connect(readonly=True) as db:
            dispatch=db.execute('SELECT request_digest FROM native_final_budget_dispatches WHERE scope_key=? AND sequence=?',
                (ledger.scope_key,pending['sequence'])).fetchone()
        _need(dispatch is not None and source.get('native_request_digest')==dispatch['request_digest'],
              'ORIGINAL_WIRE_AND_LEDGER_REQUEST_CHANGED')
        # This appends confirmation of that SAME original request. Its initial
        # UNKNOWN reservation and charged attempt are immutable and retained.
        ledger.complete_reserved('create_publish_task','ACCEPTED')
        actions=_plan_actions(material)
        required=sum(len(material['image_urls']) if name=='UPLOAD_APPROVED_IMAGES' else 1 for name in actions)
        remaining=budget['limits']['per_target_maximum']-budget['attempts']['per_target'][target['target_label']]
        _need(required<=remaining,'REMAINING_FROZEN_BUDGET_INSUFFICIENT')
        _need(all(not any(r['operation']==_OPERATIONS[name] for r in own) for name in actions),
              'PRIOR_REMAINING_STEP_REQUIRES_READONLY_RECONCILIATION')
        checkpoint=ShopeePublicationCheckpointStore(native_request.checkpoint_root,native_request)
        checkpoint.record('DISPATCH',{'target_label':target['target_label'],'attempted':False,'accepted':True,
            'outcome':'ACCEPTED','provider_task_id':source['task_id'],'existing_item_id':material['item_id'],
            'message':'original task confirmed by exact official GET; original attempt and initial UNKNOWN preserved',
            'external_write_count':0})
        references=[]
        from shared_platform.native_canonical_provider_edges import shopee_dependencies
        runtime=shopee_dependencies(ledger,native_request.checkpoint_root,references).runtime
        context=runtime.context(source['resource']['shop_region'])
        _need(context.shop_id==source['resource']['shop_id']
            and str(context.merchant_id)==source['account']['merchant_id'],'ORIGINAL_ACCOUNT_CHANGED')
        ids=material['image_ids'];performed=[]
        def execute(_request):
            nonlocal ids
            for action in actions:
                # Recheck the unchanged full original decision before every
                # reservation. The ledger performs its own final short CAS.
                current,_,_=_approved_inputs(active,adapter_request)
                _need(current['payload_digest']==plan['payload_digest'],'CURRENT_SOURCE_CHANGED')
                operation=_OPERATIONS[action]
                if action=='UPLOAD_APPROVED_IMAGES':
                    value=runtime.upload_regional_images(context,material['image_urls'],
                        before_upload=lambda:ledger.reserve_target(target['target_label'],operation))
                    ids=tuple(value['image_ids'])
                    _need(len(ids)==len(material['image_urls']) and len(set(ids))==len(ids),'UPLOADED_IMAGE_IDENTITIES_INCOMPLETE')
                else:
                    ledger.reserve_target(target['target_label'],operation)
                    if action=='UPDATE_APPROVED_IMAGES':runtime.update_regional_images(context,material['item_id'],image_ids=ids)
                    elif action=='UPDATE_APPROVED_COPY':runtime.update_regional_copy(context,material['item_id'],title=material['title'],description=material['description'])
                    elif action=='UPDATE_APPROVED_DESCRIPTION':runtime.update_regional_description(context,material['item_id'],description=material['description'],image_ids=ids)
                    elif action=='LIST_ORIGINAL_ITEM':runtime.list_item(context,material['item_id'])
                    else:raise ApprovalBlocked('NATIVE_ORIGINAL_CONTINUATION_UNCLASSIFIED_ACTION')
                performed.append(action)
            return readback._provider_official_reads(active,source)
        final_responses=retained_provider_executor(execute)(native_request)
        evidence=readback._provider_verified_evidence(facts,source,final_responses)
        current,_,_=_approved_inputs(active,adapter_request)
        _need(current['payload_digest']==plan['payload_digest'],'CURRENT_SOURCE_CHANGED_AFTER_READ')
        _need(readback._provider_original_task(active,facts)==source,'ORIGINAL_RESPONSE_CHANGED')
        from shared_platform.catalog_publication_sync import capture_readback
        capture_readback(native_request,[row for target_row in evidence['canonical_platform_result']['targets']
            for row in target_row['evidence']['official_catalog_rows']])
        checkpoint.record('READBACK',{'target_label':target['target_label'],'attempted':False,'accepted':True,
            'outcome':'PUBLISHED','provider_task_id':source['task_id'],'item_id':material['item_id'],
            'message':'original task and only remaining frozen actions officially verified',
            'external_write_count':sum(len(material['image_urls']) if a=='UPLOAD_APPROVED_IMAGES' else 1 for a in performed)})
        canonical=evidence['canonical_platform_result']
        final_budget=ledger.snapshot()
        _need(any(r['operation']=='create_publish_task' and r['outcome']=='ACCEPTED'
            and r['sequence']==unknown[0]['sequence'] for r in final_budget['reservations']),
            'ORIGINAL_REQUEST_CONFIRMATION_REQUIRED')
        proof={'schema_version':'native-scoped-v4-target-result/v1','target_label':target['target_label'],
            'verified':True,'status':'PUBLISHED','outcome_unknown':False,'reconciliation_required':False,
            'request_attempted':True,'known_pre_submit_zero':False,'canonical_platform_result':canonical,
            'mutation_budget':final_budget,'external_writes_performed':[target['target_label']],
            'original_sdk_result':deepcopy(result),'provider_original_readback':evidence,
            'catalog_projection_failures':deepcopy(getattr(native_request.catalog_sink,'failures',[])),
            'approved_remaining_steps':{'planned':actions,'performed':performed,'same_original_attempt':True,
                'new_create_calls':0,'original_initial_unknown_preserved':True,
                'original_outcome_confirmation_appended':True}}
        return AdapterExecutionResult(True,True,'Original task and approved remaining steps verified',
            f"shopee:{context.shop_id}:item:{material['item_id']}",proof)
    except (ValueError,OSError,KeyError,TypeError,RuntimeError) as error:
        # Original target/result/checkpoint/UNKNOWN budget remain the durable
        # reconciliation source. Never fall back to the full publish executor.
        if type(result) is dict:
            reason=str(error)
            if not re.fullmatch(r'[A-Z][A-Z0-9_]{1,180}',reason):reason='NATIVE_ORIGINAL_CONTINUATION_LOCAL_HOLD'
            result['original_task_continuation']={'state':'HOLD','blocked_by':[reason],
                'next_action':'RECONCILE_ORIGINAL_TARGET_READONLY','new_create_calls':0,
                'original_unknown_preserved':True}
        return None
