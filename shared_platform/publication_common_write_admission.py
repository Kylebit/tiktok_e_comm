"""Server-owned R3 COMMON write admission shared by HTTP and task execution.

Service-owned local standing intent may be observed here. Complete native
account/write-history/installation receipts remain missing. A retained plan,
caller assertion or offline export classifier cannot supply those facts; every
R3 COMMON provider entry remains closed.
"""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import sqlite3


SCHEMA = 'common-technical-admission/v1'


def _retained_source_facts(plan, binding, store):
    """Observe an existing COMMON origin/history; never initialize a store.

    The only input store is supplied by the service. Its original reader checks
    retained bytes and identities inside one read-only SQLite transaction.
    Local retention does not establish account, coverage or write authority.
    """
    from shared_platform.release_store import ReleaseStore
    from shared_platform.r3_common_source_facts import (
        CommonPreparationSourceGraph, NativeCommonSourceReader,
    )
    from shared_platform.r3_frozen_review_producer import DomainReviewBlocked, _bytes

    result = {'schema_version': 'common-retained-source-diagnostic/v1',
              'status': 'UNKNOWN', 'reason': 'COMMON_SOURCE_STORE_UNAVAILABLE',
              'source_coverage': 'UNKNOWN', 'official_provenance': 'UNKNOWN',
              'budget_status': 'UNKNOWN', 'execution_authority': False}
    if type(store) is not ReleaseStore:
        return result
    if any(type(value) is not str or not value for value in binding.values()):
        return {**result, 'reason': 'COMMON_SOURCE_BINDING_UNAVAILABLE'}
    db = None
    try:
        if not store.path.is_file():
            return result
        db = store._connect_readonly()
        db.execute('BEGIN')
        reader = NativeCommonSourceReader(store)
        reader.validate_context(db)
        origin = db.execute('SELECT plan_id FROM release_plans WHERE plan_id=?',
                            (binding['plan_id'],)).fetchone()
        if origin is None:
            return {**result, 'reason': 'COMMON_SOURCE_PLAN_NOT_PERSISTED'}
        facts = reader.read_source_facts(db, binding['plan_id'])
        graph = facts.graph
        try:
            payload_bytes = _bytes(plan.get('payload'))
        except (TypeError, ValueError):
            raise DomainReviewBlocked('COMMON_SOURCE_ADMISSION_ORIGIN_MISMATCH') from None
        if (type(graph) is not CommonPreparationSourceGraph
                or graph.offer_id != binding['offer_id']
                or graph.common_plan_id != binding['plan_id']
                or graph.common_payload_digest != binding['payload_digest']
                or payload_bytes != graph.common_payload_bytes):
            raise DomainReviewBlocked('COMMON_SOURCE_ADMISSION_ORIGIN_MISMATCH')
        records = []
        for record in facts.records:
            observed = record.transport_observation
            records.append({
                'table': record.table, 'identity': list(record.identity),
                'record_digest': hashlib.sha256(record.record_bytes).hexdigest(),
                'evidence_digest': record.evidence_digest,
                'transport_observed': observed is not None,
                'wire_digest': hashlib.sha256(observed.wire_bytes).hexdigest() if observed else None,
                'business_digest': hashlib.sha256(observed.business_bytes).hexdigest() if observed else None,
                'comparison_digest': hashlib.sha256(record.comparison_bytes).hexdigest()
                    if record.comparison_bytes is not None else None,
                'lineage_digest': hashlib.sha256(record.lineage_bytes).hexdigest()
                    if record.lineage_bytes is not None else None,
                'official_provenance': 'UNKNOWN',
            })
        from shared_platform.native_common_budget_facts import census_same_snapshot
        try:
            census = census_same_snapshot(store, db, plan['payload'], facts)
        except (ValueError, KeyError, TypeError) as error:
            census = {'schema_version': 'native-common-local-write-census/v1',
                      'status': 'INVALID', 'reason': str(error),
                      'confirmed_write_count': 'UNKNOWN', 'execution_authority': False}
        return {**result, 'status': 'RETAINED_IDENTITY_VERIFIED', 'reason': None,
                'source_coverage': facts.source_coverage, 'origin_binding': dict(binding),
                'round1_snapshot_digest': graph.round1_snapshot_digest,
                'round2_identity_digest': hashlib.sha256(graph.round2_identity_bytes).hexdigest(),
                'targets': list(graph.targets), 'common_plan_ids': list(facts.common_plan_ids),
                'unstarted_common_plan_ids': list(facts.unstarted_common_plan_ids),
                'records': records, 'native_local_census': census}
    except DomainReviewBlocked as error:
        return {**result, 'status': 'INVALID', 'reason': str(error)}
    except (sqlite3.Error, OSError):
        return {**result, 'reason': 'COMMON_SOURCE_SNAPSHOT_UNAVAILABLE'}
    finally:
        if db is not None:
            db.close()


def inspect_common_write_admission(plan: Mapping | None, *, store=None, policy_reader=None) -> dict:
    from shared_platform.publication_common_standing_policy import inspect_service_policy
    policy = inspect_service_policy(policy_reader)
    payload = plan.get('payload') if isinstance(plan, Mapping) else None
    stage = payload.get('r3_stage_binding') if isinstance(payload, Mapping) else None
    binding = {
        'offer_id': plan.get('product_id') if isinstance(plan, Mapping) else None,
        'plan_id': plan.get('plan_id') if isinstance(plan, Mapping) else None,
        'payload_digest': plan.get('payload_digest') if isinstance(plan, Mapping) else None,
    }
    blockers = []
    if (not isinstance(stage, Mapping)
            or stage.get('schema_version') != 'r3-common-stage/v1'
            or any(not isinstance(value, str) or not value for value in binding.values())):
        blockers.append('COMMON_TECHNICAL_BINDING_INVALID')
    if policy.status != 'KNOWN_LOCAL_USER_INTENT':
        blockers.append('COMMON_STANDING_POLICY_AUTHORITY_UNKNOWN')
    else:
        blockers.extend(('COMMON_ACCOUNT_AUTHORITY_UNKNOWN',
                         'COMMON_COVERAGE_AUTHORITY_UNKNOWN',
                         'COMMON_SCHEMA_INSTALLATION_UNKNOWN'))
    blockers.append('COMMON_HISTORICAL_WRITE_BUDGET_UNKNOWN')
    return {
        'schema_version': SCHEMA,
        'status': 'BLOCKED',
        'binding': binding,
        'source_facts': _retained_source_facts(plan, binding, store),
        'standing_policy_facts': policy.diagnostic(),
        'authority_facts': {
            'account_authority': 'UNKNOWN', 'standing_policy_authority': policy.status,
            'coverage_authority': 'UNKNOWN', 'schema_installation': 'UNKNOWN',
        },
        'blockers': blockers,
        'final_review_available': False,
        'execution_authority': False,
        'external_writes_performed': [],
    }
