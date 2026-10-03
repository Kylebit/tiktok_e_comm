"""Same-store preparation lineage and local write census, never write authority.

No constructor/caller proof, local row count or retained transport grants account
permissions or attests external-history coverage. All reads use the service's
existing read-only SQLite snapshot; this module creates no database or ledger.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import sqlite3

from shared_platform.round1_category_evidence import digest

SCHEMA = 'native-common-preparation-source/v1'
COMMON = 'miaoshou:COMMON'
_READBACK_CHECKS = {'title', 'seller_sku', 'selected_sku_keys', 'selected_sku_numbers',
    'spec_labels', 'spec_label_binding', 'weight', 'dimensions', 'images',
    'description_notes', 'description_image_count', 'video_action', 'common_id',
    'source_identity', 'detail_binding', 'sku_logistics'}


@dataclass(frozen=True)
class NativePreparationSource:
    document_bytes: bytes

    def payload(self):
        return json.loads(self.document_bytes)


def _stored_preparation(db, offer, reference):
    from shared_platform import round1_workspace as workspace
    row = db.execute('SELECT * FROM round1_workspace_preparations '
                     'WHERE offer_id=? AND reference=?', (offer, reference)).fetchone()
    return workspace._decode(row, connection=db)


def _source_payload(db, document, snapshot):
    from shared_platform import round1_workspace as workspace
    from shared_platform import publication_rounds as rounds
    if type(snapshot) is not dict:
        raise ValueError('COMMON_NATIVE_PREPARATION_SNAPSHOT_INVALID')
    unsigned = {key: value for key, value in snapshot.items() if key != 'snapshot_digest'}
    if (snapshot.get('schema_version') != rounds.ROUND1_SCHEMA
            or snapshot.get('snapshot_digest') != rounds.canonical_digest(unsigned)
            or document['scope']['offer_id'] != snapshot.get('offer_id')
            or sorted(document['scope']['requested_targets']) != sorted(snapshot['canonical_targets'])
            or digest(document['packet']) != snapshot.get('first_review_digest')
            or document['approval_fingerprint'] != snapshot.get('product_approval_fingerprint')):
        raise ValueError('COMMON_NATIVE_PREPARATION_SOURCE_CONFLICT')
    review = document['packet']
    frozen_facts = {key: deepcopy(review.get(key) or ({} if key in
        {'product_facts', 'shared_review_facts', 'publication_stock_policy', 'content_groups'} else []))
        for key in ('product_facts', 'shared_review_facts', 'publication_stock_policy',
                    'targets', 'platform_categories', 'copy_review_sets', 'content_groups')}
    if 'category_evidence_binding' in review:
        frozen_facts['category_evidence_binding'] = deepcopy(review['category_evidence_binding'])
    if snapshot.get('fact_snapshot') != frozen_facts:
        raise ValueError('COMMON_NATIVE_PREPARATION_SOURCE_CONFLICT')
    root = workspace._check_preparation_root(document, connection=db)
    return {'schema_version': SCHEMA, 'offer_id': document['scope']['offer_id'],
            'prepared_reference': document['reference'],
            'preparation_digest': digest(document),
            'round1_snapshot_digest': snapshot['snapshot_digest'],
            'round1_snapshot': deepcopy(snapshot),
            'targets': sorted(document['scope']['requested_targets']),
            'account_identity_digest': document['scope']['account_identity_digest'],
            'preparation_root': root, 'execution_authority': False}


def read_preparation_source(store, documents, state):
    """The planner supplies actual state and already validated R1/R2 sources."""
    from shared_platform.release_store import ReleaseStore
    from shared_platform import publication_rounds as rounds
    approval = state.get('product_approval') or {}
    reference = approval.get('round1_prepared_reference')
    if reference is None:
        return None  # Legacy preview retains no fabricated source or root.
    if (type(store) is not ReleaseStore or not store.path.is_file()
            or type(reference) is not str or not reference):
        raise ValueError('COMMON_NATIVE_PREPARATION_SOURCE_UNAVAILABLE')
    r1 = documents['round1_snapshot']
    if rounds.validate_round2_input(r1['offer_id'], state) != r1:
        raise ValueError('COMMON_NATIVE_PREPARATION_SNAPSHOT_CHANGED')
    try:
        with store._connect_readonly() as db:
            db.execute('BEGIN')
            document = _stored_preparation(db, r1['offer_id'], reference)
            if (document['scope']['offer_id'] != r1['offer_id']
                    or sorted(document['scope']['requested_targets']) != sorted(r1['canonical_targets'])
                    or digest(document['packet']) != r1['first_review_digest']
                    or approval.get('round1_review_digest') != r1['first_review_digest']
                    or document['approval_fingerprint'] != r1['product_approval_fingerprint']):
                raise ValueError('COMMON_NATIVE_PREPARATION_SOURCE_CONFLICT')
            value = _source_payload(db, document, r1)
            return NativePreparationSource(json.dumps(value, ensure_ascii=False, sort_keys=True,
                separators=(',', ':'), allow_nan=False).encode('utf-8'))
    except sqlite3.Error as error:
        raise ValueError('COMMON_NATIVE_PREPARATION_SOURCE_UNAVAILABLE') from error


def _validate_plan_source(db, payload):
    stage = payload.get('r3_stage_binding') or {}
    source = stage.get('native_preparation_source')
    if source is None:
        return None
    if (type(source) is not dict or source.get('schema_version') != SCHEMA
            or source.get('execution_authority') is not False
            or source.get('offer_id') != payload['product_id']):
        raise ValueError('COMMON_NATIVE_PLAN_SOURCE_INVALID')
    document = _stored_preparation(db, payload['product_id'], source.get('prepared_reference'))
    expected = _source_payload(db, document, source.get('round1_snapshot'))
    if source != expected:
        raise ValueError('COMMON_NATIVE_PLAN_SOURCE_CHANGED')
    if (source['round1_snapshot_digest'] != stage['round1_snapshot_digest']
            or source['targets'] != sorted(stage['marketplace_targets'])
            or stage['r2_identity']['round1_snapshot_digest'] != source['round1_snapshot_digest']):
        raise ValueError('COMMON_NATIVE_PLAN_SOURCE_CHANGED')
    return source


def validate_preparation_source(store, source):
    """A typed constructor alone is not provenance, even for plan binding."""
    from shared_platform.release_store import ReleaseStore
    if type(store) is not ReleaseStore or not store.path.is_file():
        raise ValueError('COMMON_NATIVE_PREPARATION_SOURCE_UNAVAILABLE')
    value = source.payload()
    try:
        with store._connect_readonly() as db:
            db.execute('BEGIN')
            document = _stored_preparation(db, value['offer_id'], value['prepared_reference'])
            if value != _source_payload(db, document, value['round1_snapshot']):
                raise ValueError('COMMON_NATIVE_PREPARATION_SOURCE_CONFLICT')
    except sqlite3.Error as error:
        raise ValueError('COMMON_NATIVE_PREPARATION_SOURCE_UNAVAILABLE') from error


def _locally_checked_readback(record, target, payload):
    if not record or not record.transport_observation or not record.lineage_bytes:
        return False
    evidence = json.loads(record.evidence_bytes)
    checks = evidence.get('checks')
    return bool(evidence.get('source') in {'miaoshou_open_api', 'miaoshou_common_readonly_detail'}
        and evidence.get('verified') is True and type(checks) is dict
        and _READBACK_CHECKS.issubset(checks) and all(value is True for value in checks.values())
        and evidence.get('offer_id') == payload['product_id']
        and str(target.get('external_id')) == payload['product_id']
        and evidence.get('image_count') == len(payload.get('images') or [])
        and json.loads(record.record_bytes).get('verified_at'))


def census_same_snapshot(store, db, payload, source_facts):
    """Classify real local attempts; complete budget/coverage remain UNKNOWN.

    Confirmation count is not attempt count, and read-only reuse is not a write.
    Unknown outcomes anywhere on this Offer remain visible across preparation
    roots. A new request/task/reference therefore cannot erase an active result.
    """
    from shared_platform.r3_common_source_facts import NativeCommonSourceReader
    NativeCommonSourceReader(store).validate_context(db)
    result = {'schema_version': 'native-common-local-write-census/v1',
              'status': 'UNKNOWN', 'reason': 'LEGACY_PREPARATION_ROOT_UNKNOWN',
              'confirmed_write_count': 'UNKNOWN', 'local_observed_confirmed_writes': 0,
              'local_observed_readonly_reuses': 0, 'unresolved_attempts': [],
              'unclassified_history': [],
              'source_coverage': 'LOCAL_RETAINED_ONLY', 'coverage_authority': 'UNKNOWN',
              'account_authority': 'UNKNOWN', 'schema_installation': 'UNKNOWN',
              'budget_authority': 'UNKNOWN', 'execution_authority': False}
    source = _validate_plan_source(db, payload)
    root = (source or {}).get('preparation_root') or {}
    if root.get('status') != 'PERSISTED_NATIVE_PREPARATION_ROOT':
        return result
    records = source_facts.records
    if len(records) > 20000:
        return {**result, 'reason': 'COMMON_LOCAL_CENSUS_LIMIT_EXCEEDED'}
    plans = {record.identity[0]: json.loads(record.evidence_bytes)
             for record in records if record.table == 'release_plans'}
    runs = {record.identity[0]: json.loads(record.record_bytes)
            for record in records if record.table == 'release_runs'}
    readbacks = {record.identity[0]: record for record in records
                 if record.table == 'release_target_readbacks'}
    failures = {(record.identity[0], int(record.identity[2])): record for record in records
                if record.table == 'release_target_failure_events'}
    submissions = {record.identity[0] for record in records
                   if record.table == 'release_target_submissions'}
    targets = {record.identity[0]: json.loads(record.record_bytes) for record in records
               if record.table == 'release_target_runs'}
    confirmed = 0
    reuses = 0
    for record in records:
        if record.table != 'release_target_runs':
            continue
        target = json.loads(record.record_bytes)
        run_id = target['run_id']
        other = _validate_plan_source(db, plans[runs[run_id]['plan_id']])
        other_root = (other or {}).get('preparation_root') or {}
        same_root = (other_root['root_id'] == root['root_id']
            if other_root.get('status') == 'PERSISTED_NATIVE_PREPARATION_ROOT' else None)
        attempts = target['attempts']
        if type(attempts) is not int or not 0 <= attempts <= 1000:
            return {**result, 'reason': 'COMMON_LOCAL_CENSUS_LIMIT_EXCEEDED'}
        readback = readbacks.get(run_id)
        evidence = json.loads(readback.evidence_bytes) if readback else None
        native = _locally_checked_readback(readback, target, plans[runs[run_id]['plan_id']])
        current_confirmed = bool(native and target['status'] == 'SUCCEEDED'
            and evidence.get('mode') != 'readback_reuse_no_write'
            and evidence.get('external_writes_performed') == ['miaoshou:COMMON:immutable_plan_write'])
        current_reuse = bool(native and target['status'] == 'SUCCEEDED'
            and evidence.get('mode') == 'readback_reuse_no_write'
            and evidence.get('external_writes_performed') == [] and type(evidence.get('predecessor')) is dict)
        if current_confirmed:
            from shared_platform import native_common_technical_execution as technical
            from shared_platform.release_store import ReleaseAuthorizationError
            stored_run = runs[run_id]
            admission = json.loads(stored_run.get('technical_admission_json') or '{}')
            if admission.get('native_account_context') is not None:
                try:
                    facts = technical._facts_for_existing_run(db, stored_run['plan_id'], run_id)
                    technical._check_readback_acceptance_reference(db, facts, stored_run, attempts, evidence)
                except (ValueError, TypeError, KeyError, ReleaseAuthorizationError):
                    current_confirmed = False
        if current_reuse:
            predecessor = evidence['predecessor']
            old_run = runs.get(predecessor.get('run_id')) or {}
            old_plan = plans.get(old_run.get('plan_id')) or {}
            old_target = targets.get(predecessor.get('run_id')) or {}
            old_readback = readbacks.get(predecessor.get('run_id'))
            current_reuse = bool(_locally_checked_readback(old_readback, old_target, old_plan)
                and old_run.get('plan_id') == predecessor.get('plan_id')
                and old_plan.get('product_id') == source['offer_id']
                and old_target.get('status') == predecessor.get('common_status') == 'SUCCEEDED'
                and str(old_target.get('external_id')) == str(predecessor.get('common_external_id')) == source['offer_id']
                and old_readback.evidence_digest == predecessor.get('common_readback_evidence_digest')
                and json.loads(old_readback.record_bytes)['verified_at'] == predecessor.get('common_readback_verified_at')
                and json.loads(next(record.record_bytes for record in records
                    if record.table == 'release_plans' and record.identity[0] == old_run['plan_id']))['payload_digest']
                    == predecessor.get('payload_digest'))
            admission = json.loads(runs[run_id].get('technical_admission_json') or '{}')
            if current_reuse and admission.get('native_account_context') is not None:
                from shared_platform.r3_common_source_facts import NativeCommonSourceReader
                from shared_platform.native_common_retained_completion import read_retained_completion
                from shared_platform.r3_frozen_review_producer import DomainReviewBlocked
                from shared_platform.release_store import ReleaseAuthorizationError
                try:
                    completion = read_retained_completion(NativeCommonSourceReader(store), db,
                        runs[run_id]['plan_id'], run_id)
                    current_reuse = completion.state == 'READONLY_REUSE'
                except (ValueError, TypeError, KeyError, DomainReviewBlocked, ReleaseAuthorizationError):
                    current_reuse = False
        if same_root and current_confirmed:
            confirmed += 1
        if same_root and current_reuse:
            reuses += 1
        for attempt in range(1, attempts + 1):
            if attempt == attempts and (current_confirmed or current_reuse):
                continue
            failed = failures.get((run_id, attempt))
            failure = json.loads(failed.evidence_bytes) if failed else {}
            not_dispatched = (failure.get('schema_version') in
                {'common-local-config-not-dispatched/v1', 'common-local-variant-key-not-dispatched/v1'}
                and bool(failure.get('source')) and failure.get('request_attempted') is False
                and type(failure.get('external_write_count')) is int and failure['external_write_count'] == 0
                and failure.get('write_outcome') == 'not_dispatched'
                and failure.get('external_writes_performed') == [])
            if not not_dispatched:
                observed = {'run_id': run_id, 'attempt': attempt,
                            'same_preparation_root': same_root, 'status': target['status']}
                # An unproven historical terminal row is not a new confirmed
                # write and is not automatically an active submission forever.
                field = ('unclassified_history' if target['status'] in {'SUCCEEDED', 'SUPERSEDED'}
                         else 'unresolved_attempts')
                result[field].append(observed)
        if run_id in submissions and not (current_confirmed or current_reuse):
            result['unresolved_attempts'].append({'run_id': run_id, 'attempt': attempts,
                'same_preparation_root': same_root, 'status': 'SUBMITTED_UNVERIFIED'})
    return {**result, 'status': 'LOCAL_PERSISTED_CENSUS', 'reason': None,
            'preparation_root': deepcopy(root), 'local_observed_confirmed_writes': confirmed,
            'local_observed_readonly_reuses': reuses}
