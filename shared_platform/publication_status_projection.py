"""Read-only projection of the existing approved plan, async runs and reports."""
from __future__ import annotations

from datetime import datetime, timezone
import sqlite3

from shared_platform.product_publication_reports import ProductPublicationReportError, _digest, validate_publication_report
from shared_platform.product_publication_runs import ProductPublicationRunError
from shared_platform.product_publication_run_reconciliations import (
    ProductPublicationRunReconciliationError,
    ProductPublicationRunReconciliationStore,
)


def _time(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('TIMESTAMP_UNZONED')
    return parsed.astimezone(timezone.utc)


def _digest_equal(left, right):
    return isinstance(left, str) and isinstance(right, str) and left.removeprefix('sha256:') == right.removeprefix('sha256:')


def _strict_zero_dispatch_without_target_evidence(report, targets):
    """Accept only a complete report-level proof that no provider call was budgeted."""
    try:
        evidence = report['summary']['evidence']
        labels = [row['target_label'] for row in targets]
        budgets = report['mutation_budgets']
        if (report['status'] != 'FAILED' or evidence['dispatch_attempted'] is not False
                or type(evidence['external_write_count']) is not int
                or evidence['external_write_count'] != 0
                or not targets or any(row['status'] != 'FAILED' or row.get('evidence') is not None for row in targets)
                or not isinstance(budgets, list) or len(budgets) != 1
                or budgets[0]['platform'] != report['summary']['platforms'][0]['platform']
                or budgets[0]['reservations'] != []):
            return False
        attempts = budgets[0]['attempts']
        per_target = attempts['per_target']
        return (type(attempts['shared']) is int and attempts['shared'] == 0
                and type(attempts['total']) is int and attempts['total'] == 0
                and isinstance(per_target, dict) and set(per_target) == set(labels)
                and all(type(value) is int and value == 0 for value in per_target.values()))
    except (KeyError, TypeError, IndexError):
        return False


def pending_targets(labels, *, status='NOT_RUN', blocker=None):
    return [{'target_label': label, 'status': status, 'reported_status': None,
             'lifecycle': 'NOT_STARTED' if status == 'NOT_RUN' else 'RECONCILIATION_REQUIRED',
             'official_success': False, 'readback_completed': None, 'request_attempted': None,
             'outcome_unknown': None, 'external_write_count': None,
             'next_action': 'AWAIT_FINAL_APPROVAL' if status == 'NOT_RUN' else 'RECONCILE_EXISTING_RUN',
             'source': None, 'history': [], 'blockers': [blocker] if blocker else []}
            for label in labels]


def project_execution(*, plan, snapshot, candidate, approval, run_store, report_store,
                      authority_root=None, reconciliation_store=None):
    """Never dispatch, reconcile with a provider, or persist a projection."""
    labels = list(plan['targets'])
    if reconciliation_store is None and getattr(run_store, 'path', None) is not None:
        reconciliation_store = ProductPublicationRunReconciliationStore(run_store.path)
    by_platform = {p: [t for t in labels if t.split(':')[0].upper() == p]
                   for p in dict.fromkeys(t.split(':')[0].upper() for t in labels)}
    histories = {label: [] for label in labels}
    runs_public, blockers = [], []

    def fail(code, affected, run=None):
        blockers.append(code)
        source = {'run_id': run['run_id'], 'report_id': run['report_id'],
                  'run_created_at': run['created_at']} if run else None
        for label in affected:
            row = pending_targets([label], status='RECONCILIATION_REQUIRED', blocker=code)[0]
            row['source'] = source
            try:
                row['_time'] = _time(run['created_at']) if run else None
            except (ValueError, TypeError, AttributeError):
                row['_time'] = None
            row['_unresolved'] = True
            histories[label].append(row)

    try:
        runs = run_store.list_runs_for_plan(offer_id=plan['product_id'], plan_id=plan['plan_id'])
        refs = report_store.list_report_refs_for_plan(offer_id=plan['product_id'], plan_id=plan['plan_id'])
    except (ProductPublicationRunError, ProductPublicationReportError, sqlite3.Error, ValueError, TypeError):
        runs, refs = [], []
        fail('RUN_OR_REPORT_INDEX_INVALID', labels)
    run_ids = {run['run_id'] for run in runs}
    if any(ref['run_id'] not in run_ids for ref in refs):
        fail('REPORT_WITHOUT_ASYNC_RUN', labels)
    for run in runs:
        affected = [label for p in run['platform_scope'] for label in by_platform.get(p, [])]
        public = {'run_id': run['run_id'], 'report_id': run['report_id'], 'state': run['state'],
                  'created_at': run['created_at'], 'updated_at': run['updated_at'],
                  'external_write_count': None, 'mutation_budgets': None, 'report_digest': None,
                  'reconciliation_digest': None}
        runs_public.append(public)
        try:
            started = _time(run['created_at'])
            if (not affected or set(run['platform_scope']) - set(by_platform)
                    or run['offer_id'] != plan['product_id'] or run['plan_id'] != plan['plan_id']
                    or run['revision'] != snapshot['product_revision']
                    or not _digest_equal(run['snapshot_digest'], snapshot['snapshot_digest'])
                    or started < _time(plan['approval']['approved_at'])
                    or _time(run['updated_at']) < started):
                raise ValueError('RUN_IDENTITY_OR_TIME_CONFLICT')
            report = report_store.get_report_by_run(run_id=run['run_id'])
            if report is None:
                receipt = reconciliation_store.get(run_id=run['run_id']) if reconciliation_store else None
                if receipt is not None:
                    identity = receipt['run_identity']
                    expected_identity = {key: run[key] for key in (
                        'run_id', 'report_id', 'offer_id', 'revision', 'plan_id',
                        'snapshot_digest', 'platform_scope', 'target_count', 'execution_identity')}
                    if (run['state'] != 'FAILED' or identity != expected_identity
                            or receipt['ordered_target_labels'] != affected
                            or receipt['provider_request_attempted'] is not False
                            or receipt['external_write_count'] != 0):
                        raise ValueError('RECONCILIATION_IDENTITY_CONFLICT')
                    public.update(external_write_count=0,
                                  reconciliation_digest=receipt['receipt_digest'])
                    for label in affected:
                        row = {'target_label': label, 'status': 'FAILED',
                               'reported_status': 'FAILED',
                               'lifecycle': 'RECONCILED_ZERO_WRITE',
                               'official_success': False, 'readback_completed': True,
                               'request_attempted': False, 'outcome_unknown': False,
                               'external_write_count': 0,
                               'next_action': 'READ_EXISTING_RECONCILIATION',
                               'source': {'run_id': run['run_id'], 'report_id': run['report_id'],
                                  'run_created_at': run['created_at'],
                                  'reconciliation_digest': receipt['receipt_digest']},
                               'blockers': [], '_time': started, '_unresolved': False}
                        histories[label].append(row)
                    continue
                if run['state'] not in {'QUEUED', 'RUNNING'}:
                    raise ValueError('TERMINAL_RUN_REPORT_MISSING')
                for label in affected:
                    row = pending_targets([label])[0]
                    row.update(status=run['state'], lifecycle=run['state'],
                        next_action='READ_EXISTING_RUN', source={'run_id':run['run_id'],
                            'report_id':run['report_id'],'run_created_at':run['created_at']},
                        _time=started, _unresolved=True)
                    if run['target_count'] != len(affected):
                        row['blockers'] = ['RUN_TARGET_SCOPE_UNRESOLVED']
                    histories[label].append(row)
                continue
            validate_publication_report({key:value for key,value in report.items()
                if key not in {'report_path','summary_digest','created_at','updated_at'}})
            if (report['offer_id'] != plan['product_id'] or report['plan_id'] != plan['plan_id']
                    or report['revision'] != snapshot['product_revision']
                    or report['run_id'] != run['run_id'] or report['report_id'] != run['report_id']
                    or not _digest_equal(report['snapshot']['digest'], snapshot['snapshot_digest'])
                    or report.get('execution_identity') != run['execution_identity']):
                raise ValueError('REPORT_IDENTITY_OR_AUTHORITY_CONFLICT')
            observed = _time(report['created_at'])
            if (observed < started or _time(report['updated_at']) != observed
                    or (run['state'] == 'COMPLETED' and observed > _time(run['updated_at']))
                    or run['state'] == 'QUEUED'):
                raise ValueError('REPORT_RUN_TIME_CONFLICT')
            targets = report.get('targets')
            if (not isinstance(targets, list) or not targets
                    or len(targets) != run['target_count']
                    or len({row['target_label'] for row in targets}) != len(targets)
                    or {row['target_label'] for row in targets} - set(affected)
                    or {row['target_label'].split(':')[0].upper() for row in targets} != set(run['platform_scope'])
                    or {row['platform'] for row in report['summary']['platforms']} != set(run['platform_scope'])):
                raise ValueError('REPORT_TARGET_SCOPE_CONFLICT')
            for platform in report['summary']['platforms']:
                selected = [t for t in targets if t['target_label'].split(':')[0].upper() == platform['platform']]
                if (platform['target_count'] != len(selected)
                        or any(platform[key] != sum(t['status']==status for t in selected)
                               for key,status in [('verified_count','PUBLISHED'),('processing_count','PROCESSING'),('failed_count','FAILED')])):
                    raise ValueError('REPORT_TARGET_COUNT_CONFLICT')
            from shared_platform import publication_autopilot as authority
            report_authority = report.get('release_authorization') or {}
            packet = (candidate, approval)
            if report_authority != {'candidate_digest':candidate['candidate_digest'], 'approval_digest':approval['approval_digest']}:
                if authority_root is None or not report_authority.get('candidate_digest'):
                    raise ValueError('REPORT_AUTHORITY_UNAVAILABLE')
                packet = authority.resolve_persisted_execution_authority(snapshot=snapshot,
                    platform_scope=run['platform_scope'], target_labels=tuple(t['target_label'] for t in targets),
                    candidate_digest=report_authority['candidate_digest'], reports_root=authority_root)
            verified_candidate = authority.validate_release_candidate_for_execution(packet[0], snapshot=snapshot,
                platform_scope=run['platform_scope'], target_labels=tuple(t['target_label'] for t in targets))
            verified_approval = authority.validate_final_approval_receipt(
                packet[1], verified_candidate, snapshot=snapshot
            )
            if started < _time(verified_approval['approved_at']):
                raise ValueError('RUN_PRECEDES_FINAL_APPROVAL')
            if report_authority != {'candidate_digest':verified_candidate['candidate_digest'], 'approval_digest':verified_approval['approval_digest']}:
                raise ValueError('REPORT_AUTHORITY_CONFLICT')
            summary = report['summary']['evidence']
            public.update(external_write_count=summary['external_write_count'],
                          mutation_budgets=report.get('mutation_budgets'), report_digest=_digest(report))
            for target in targets:
                label = target['target_label']
                evidence = target.get('evidence')
                if not isinstance(evidence, dict):
                    if _strict_zero_dispatch_without_target_evidence(report, targets):
                        row = {'target_label': label, 'status': 'FAILED',
                               'reported_status': 'FAILED', 'lifecycle': 'REPORT_RECORDED',
                               'official_success': False, 'readback_completed': False,
                               'request_attempted': False, 'outcome_unknown': False,
                               'external_write_count': 0, 'next_action': 'READ_EXISTING_REPORT',
                               'source': {'run_id': run['run_id'], 'report_id': run['report_id'],
                                  'run_created_at': run['created_at'], 'report_created_at': report['created_at'],
                                  'report_digest': public['report_digest'],
                                  'summary_digest': report['summary_digest'], **report_authority},
                               'blockers': ['ZERO_DISPATCH_TARGET_EVIDENCE_ABSENT'],
                               '_time': started, '_unresolved': False}
                        histories[label].append(row)
                        continue
                    fail('TARGET_EVIDENCE_MISSING', [label], run)
                    continue
                unknown = evidence['outcome_unknown']
                manual = evidence['provider_code'] == 'MANUAL_HANDOFF_ACCEPTED' or evidence['stage'].startswith('MANUAL')
                status = target['status']
                # Report/v2 records provider claims and a round-level readback
                # flag, but has no admitted target-bound official observation.
                # Preserve those historical facts without certifying publication.
                success = False
                from shared_platform.publication_target_observations import consume_target
                observation = consume_target(report, label, getattr(report_store, 'target_observation_reader', None))
                missing_observation = status in {'PUBLISHED', 'PROCESSING'}
                lifecycle = ('RECONCILIATION_REQUIRED' if unknown or manual or missing_observation
                             else 'REPORT_RECORDED')
                row = {'target_label':label, 'status': 'RECONCILIATION_REQUIRED' if lifecycle == 'RECONCILIATION_REQUIRED' else status,
                        **(observation if 'target_observations' in report else {}),
                       'reported_status':status, 'lifecycle':lifecycle, 'official_success':success,
                        'readback_completed':None, 'reported_readback_completed':summary['readback_completed'],
                        'request_attempted':evidence['request_attempted'],
                       'outcome_unknown':unknown, 'external_write_count':evidence['external_write_count'],
                       'next_action':'RECONCILE_EXISTING_RUN' if lifecycle == 'RECONCILIATION_REQUIRED' else 'READ_EXISTING_REPORT',
                       'source':{'run_id':run['run_id'],'report_id':run['report_id'],'run_created_at':run['created_at'],
                           'report_created_at':report['created_at'],'report_digest':public['report_digest'],
                           'summary_digest':report['summary_digest'], **report_authority},
                        'blockers':['MANUAL_IS_NOT_OFFICIAL_SUCCESS'] if manual else
                             [observation['observation_blocker']] if missing_observation else [],
                       '_time':started,'_unresolved':lifecycle=='RECONCILIATION_REQUIRED'}
                histories[label].append(row)
        except (ProductPublicationReportError, ProductPublicationRunReconciliationError,
                sqlite3.Error, ValueError, TypeError, KeyError, OSError, AttributeError) as error:
            code = str(error) if type(error) is ValueError and str(error).isupper() else 'REPORT_INTEGRITY_INVALID'
            # Even corrupt/ambiguous sources must not revive an older success.
            fail(code, affected or labels, run)
    targets = []
    for label in labels:
        history = histories[label]
        if not history:
            row = pending_targets([label])[0]
            row['next_action'] = 'EXECUTE_APPROVED_PLATFORM'
        else:
            history.sort(key=lambda row: row['_time'] or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
            row = {k:v for k,v in history[0].items() if not k.startswith('_')}
            ambiguous = (len(history) > 1 and history[0]['_time'] == history[1]['_time'])
            if ambiguous or any(x['_unresolved'] for x in history[1:]):
                row.update(status='RECONCILIATION_REQUIRED', lifecycle='RECONCILIATION_REQUIRED',
                    official_success=False,next_action='RECONCILE_EXISTING_RUN',
                    outcome_unknown=True if any(x['outcome_unknown'] is True for x in history) else None,
                    external_write_count=None,
                    blockers=[*row['blockers'],'SOURCE_ORDER_CONFLICT' if ambiguous else 'EARLIER_RUN_UNRESOLVED'])
            row['history'] = [{key:x[key] for key in ('source','status','reported_status','blockers',
                'outcome_unknown','external_write_count','readback_completed')}
                | ({'reported_readback_completed':x['reported_readback_completed']}
                   if 'reported_readback_completed' in x else {}) for x in history]
        targets.append(row)
    platforms = []
    for platform, selected in by_platform.items():
        rows = [r for r in targets if r['target_label'] in selected]
        used = any(r['source'] for r in rows)
        for row in rows:
            if used and row['status'] == 'NOT_RUN': row['next_action'] = 'REVIEW_TARGET_SCOPED_ACTION'
        source_ids = list(dict.fromkeys(r['source']['run_id'] for r in rows if r['source']))
        source = next((r['source'] for r in rows if r['source']), None)
        platforms.append({'platform':platform,'status':_status(rows),
            'run_id': source['run_id'] if len(source_ids)==1 else None,
            'report_id': source['report_id'] if len(source_ids)==1 else None,
            'source_run_ids': source_ids,'next_action':
            'EXECUTE_APPROVED_PLATFORM' if not used and not any(r['blockers'] for r in rows)
            else 'RECONCILE_EXISTING_RUN' if any(r['status']=='RECONCILIATION_REQUIRED' for r in rows)
            else 'READ_EXISTING_RUN' if any(r['status'] in {'QUEUED','RUNNING'} for r in rows)
            else 'READ_EXISTING_REPORT', 'target_labels':selected})
    counts = [row['external_write_count'] for row in runs_public]
    return {'status':_status(targets),'target_results':targets,'platforms':platforms,
            'execution_summary':{'run_count':len(runs_public),'runs':runs_public,
                'external_write_count':sum(counts) if not blockers and all(c is not None for c in counts) else None,
                'official_success_count':sum(row['official_success'] for row in targets),
                'target_count':len(targets), 'index_valid': 'RUN_OR_REPORT_INDEX_INVALID' not in blockers,
                'blockers':list(dict.fromkeys(blockers))}}


def _status(rows):
    statuses = {r['status'] for r in rows}
    if 'RECONCILIATION_REQUIRED' in statuses: return 'RECONCILIATION_REQUIRED'
    if statuses & {'QUEUED','RUNNING'}: return 'PROCESSING'
    if statuses == {'NOT_RUN'}: return 'READY_TO_PUBLISH'
    if all(r['official_success'] for r in rows): return 'PUBLISHED'
    if 'FAILED' in statuses: return 'FAILED' if statuses == {'FAILED'} else 'PARTIAL'
    if any(r['lifecycle']=='READBACK_ONLY' for r in rows): return 'READBACK_ONLY'
    if 'PROCESSING' in statuses: return 'PROCESSING'
    return 'PARTIAL'
