"""Conservative continuation claims in the existing publication run ledger.

No dispatch, threads, provider clients or second ledger. Host context readers
must load pinned scope policy and frozen approval from trusted server paths.
Retry successors require a host-pinned authority root and their complete
transport/source receipt chain; a body-provided proof cannot enable them.
"""
from shared_platform.tiktok_continuation_admission import validate_admission, validate_request_shape, _digest


def claim_tiktok_continuation(*, data, completion, prepared, execution_identity,
                             run_store, report_store, context_reader, authority_root=None):
    manifest = validate_request_shape(data)
    retry = manifest.get('zero_write_retry')
    if retry is not None and authority_root is None:
        raise ValueError('zero-write retry source binding is not configured')
    if tuple(prepared.platform_scope) != ('TIKTOK',):
        raise ValueError('continuation claim requires TikTok-only scope')
    initial = validate_admission(data, completion=completion, **context_reader(data))
    if (set(prepared.target_labels_by_platform['TIKTOK']) != set(initial['approved_target_labels'])
            or prepared.offer_id != data['offer_id'] or prepared.plan_id != data['plan_id']
            or prepared.snapshot_digest != data['snapshot_digest']
            or str(prepared.revision) != str(manifest['lineage']['revision'])):
        raise ValueError('prepared scope differs from full approved continuation')
    selected = set(initial['selected_target_labels'])
    source_id = retry['source_run_id'] if retry else None if completion else manifest['source_run_id']
    allowed_sources = {source_id} if source_id else set()
    if retry and retry.get('retry_depth') == 2:
        from shared_platform.tiktok_lineage_recovery import predecessor_tiktok_approved_completion_retry_manifest
        allowed_sources.add(predecessor_tiktok_approved_completion_retry_manifest(manifest)['zero_write_retry']['source_run_id'])

    def validate_source():
        if completion and authority_root is not None:
            from shared_platform.tiktok_completion_receipts import verify_completion_receipts
            verify_completion_receipts(manifest,authority_root=authority_root,
                                       run_store=run_store,report_store=report_store)
            return
        if source_id is None:
            return
        source = run_store.get_run_by_id(run_id=source_id)
        if (not source or source['state'] != 'COMPLETED'
                or source['offer_id'] != prepared.offer_id
                or source['revision'] != prepared.revision
                or source['target_count'] != len(initial['approved_target_labels'])
                or source['plan_id'] != prepared.plan_id
                or source['snapshot_digest'] != prepared.snapshot_digest
                or source['platform_scope'] != ['TIKTOK']):
            raise ValueError('continuation source run is unresolved or differs')
        report = report_store.get_report_by_run(run_id=source_id)
        if not report or _digest(report) != manifest['source_evidence_digest']:
            raise ValueError('continuation source report identity differs')
        if (report['run_id'] != source_id or report['report_id'] != source['report_id']
                or source.get('final_report_id') != report['report_id']
                or report['offer_id'] != prepared.offer_id or report['revision'] != prepared.revision
                or report['plan_id'] != prepared.plan_id
                or report['snapshot']['digest'] != prepared.snapshot_digest
                or report.get('release_authorization') != {
                    key:manifest['lineage'][key].removeprefix('sha256:')
                    for key in ('candidate_digest','approval_digest')}):
            raise ValueError('continuation source report/run approved identity differs')
        rows = {row['target_label']:row for row in report['targets']}
        if set(rows) != set(initial['approved_target_labels']):
            raise ValueError('continuation source lacks the complete target table')
        for label in selected:
            row = rows[label]; evidence = row.get('evidence') or {}
            if (row['status'] != 'FAILED' or evidence.get('outcome_unknown') is not False
                    or evidence.get('request_attempted') is not False
                    or evidence.get('external_write_count') != 0):
                raise ValueError('continuation source is not proved zero-write failure')

    def admission_guard():
        current = validate_admission(data, completion=completion, **context_reader(data))
        if current != initial:
            raise ValueError('scope policy or frozen authority changed before claim')
        validate_source()

    def overlap_guard(prior):
        if prior['run_id'] not in allowed_sources:
            # Includes reportless/failed/queued/unknown attempts. A different
            # manifest or policy cannot evade an existing unresolved run.
            raise ValueError('continuation has an overlapping prior run; reconcile first')

    identity = {'kind':'TIKTOK_CONTINUATION',
                'authority_digest':manifest.get('authority_addendum_digest',manifest.get('authority_receipt_digest')),
                'manifest_digest':manifest['manifest_digest'],'policy_digest':initial['policy_digest']}
    if retry:
        identity={'kind':'TIKTOK_FIRST_COMPLETION_ZERO_WRITE_RETRY',
                  'authority_digest':manifest['authority_addendum_digest'],
                  'manifest_digest':manifest['manifest_digest'],'retry_of_run_id':retry['source_run_id'],
                  **{key:retry[key] for key in ('source_report_digest','source_result_digest','source_manifest_digest')}}
    key = _digest({'offer_id':prepared.offer_id,'plan_id':prepared.plan_id,
                   'snapshot_digest':prepared.snapshot_digest,'request_identity':identity,
                   'execution_identity':execution_identity,'policy_digest':initial['policy_digest']}).removeprefix('sha256:')
    return run_store.create_run(run_id='tiktok-continuation-'+key[:40],
        offer_id=prepared.offer_id,revision=prepared.revision,plan_id=prepared.plan_id,
        snapshot_digest=prepared.snapshot_digest,platform_scope=('TIKTOK',),
        target_count=len(initial['approved_target_labels']),execution_identity=execution_identity,
        request_identity=identity,approved_request_guard=overlap_guard,
        admission_guard=admission_guard,retry_of_run_id=source_id)
