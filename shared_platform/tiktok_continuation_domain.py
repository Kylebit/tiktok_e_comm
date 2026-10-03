"""Exact zero-write retry handover using the existing operations ledger.

All ten target locks move atomically to the successor, including unselected
UNKNOWN/PROCESSING targets. No lock is dropped simply because it is unselected.
"""
from shared_platform.tiktok_completion_receipts import verify_completion_receipts
from shared_platform.tiktok_continuation_admission import validate_admission


def begin_tiktok_completion_retry(*, data, context_reader, authority_root,
                                 release_store, run_store, report_store, root, run_id):
    from shared_platform.operations_domain_guard import engine_for, _publication_operation_id
    from shared_platform.internal_catalog_sku import internal_sku
    admitted=validate_admission(data,completion=True,**context_reader(data))
    manifest=data['continuation_manifest'];binding=manifest.get('zero_write_retry')
    if not binding:raise ValueError('domain successor requires exact zero-write retry proof')
    verify_completion_receipts(manifest,authority_root=authority_root,run_store=run_store,report_store=report_store)
    successor=run_store.get_run_by_id(run_id=run_id)
    expected_request={'kind':'TIKTOK_FIRST_COMPLETION_ZERO_WRITE_RETRY',
        'authority_digest':manifest['authority_addendum_digest'],'manifest_digest':manifest['manifest_digest'],
        'retry_of_run_id':binding['source_run_id'],
        **{key:binding[key] for key in ('source_report_digest','source_result_digest','source_manifest_digest')}}
    if (not successor or successor['state']!='QUEUED' or successor['request_identity']!=expected_request
            or successor['offer_id']!=data['offer_id'] or successor['plan_id']!=data['plan_id']
            or successor['snapshot_digest']!=data['snapshot_digest']
            or successor['revision']!=int(manifest['lineage']['revision'])
            or successor['platform_scope']!=['TIKTOK'] or successor['target_count']!=10):
        raise ValueError('domain successor is not the exact durable queued claim')
    snapshot=release_store.approved_publication_snapshot(offer_id=data['offer_id'],snapshot_digest=data['snapshot_digest'])
    if (not isinstance(snapshot,dict) or snapshot.get('plan_id')!=data['plan_id']
            or snapshot.get('snapshot_digest')!=data['snapshot_digest']):
        raise ValueError('domain successor snapshot identity differs')
    targets=[row['target_label'] for row in snapshot['publication_targets']
             if row['target_label'].split(':',1)[0].lower()=='tiktok']
    if len(targets)!=10 or set(targets)!=set(admitted['approved_target_labels']):
        raise ValueError('domain successor must retain all ten target locks')
    skus={internal_sku(row.get('seller_sku')) for row in snapshot['skus']}
    if len(skus)!=1 or not next(iter(skus),''):raise ValueError('domain successor must bind one internal SKU')
    sku=next(iter(skus))
    source=run_store.get_run_by_id(run_id=binding['source_run_id'])
    prior_identity=source.get('request_identity') or {}
    previous_attempt=None
    if prior_identity.get('kind')=='TIKTOK_FIRST_COMPLETION_ZERO_WRITE_RETRY':
        previous_attempt={'run_id':source['run_id'],'retry_of_run_id':prior_identity['retry_of_run_id'],
                          'source_evidence_digest':prior_identity['source_report_digest']}
    elif prior_identity.get('kind') not in {'STANDARD','TIKTOK_CONTINUATION'}:
        raise ValueError('domain predecessor request kind is unavailable')
    current_attempt={'run_id':run_id,'retry_of_run_id':binding['source_run_id'],
                     'source_evidence_digest':binding['source_report_digest']}
    previous_operation=_publication_operation_id(data['plan_id'],targets,previous_attempt)
    operation=_publication_operation_id(data['plan_id'],targets,current_attempt)
    engine=engine_for(root)
    if engine is None:raise ValueError('domain coordination is unavailable')
    owners=[task['task_id'] for task in engine.dashboard()['tasks']
            if task['template']=='publication' and task['scope'].get('offer_id')==str(data['offer_id'])
            and task['scope'].get('skus')==[sku] and set(task['scope'].get('shops',[]))==set(targets)
            and task['execution_state'] not in {'completed','cancelled'}]
    if len(owners)>1:raise ValueError('multiple domain owners require reconciliation')
    result=engine.supersede_domain_operation(previous_operation,operation,
        previous_skus=[sku],previous_shops=targets,skus=[sku],shops=targets,
        owner_task_id=owners[0] if owners else None,
        reconciliation_ref='tiktok-zero-write:'+binding['source_report_digest'])
    if not result['acquired']:raise ValueError('domain successor already exists; do not dispatch again')
    return engine,operation,result
