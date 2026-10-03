"""Read a completed native prepared baseline without reserving or editing it.

The retained facts have a useful success branch. Their READ signature context,
local count and explicit technical schema are distinct from installation/path
coverage authority. The latter cannot be manufactured by a type or constructor.
"""
from dataclasses import dataclass
import hashlib
import json
import sqlite3

from shared_platform.publication_runtime_config import StartupConfig
from shared_platform.release_store import ReleaseStore, ReleaseStoreError
from shared_platform.r3_common_source_facts import NativeCommonSourceReader
from shared_platform.native_common_retained_completion import read_retained_completion
from shared_platform.native_common_signing_context import NativeCommonSigningContextReader
from shared_platform.native_common_budget_facts import _validate_plan_source, census_same_snapshot
from shared_platform.native_common_technical_execution import _schema_installed
from shared_platform.publication_common_standing_policy import NativeCommonStandingPolicyReader, inspect_service_policy


@dataclass(frozen=True)
class NativeCommonBaselineFacts:
    status: str
    reason: str | None = None
    offer_id: str | None = None
    preparation_root_id: str | None = None
    completion_state: str | None = None
    mutation_sha256: str | None = None
    retained_readback_digest: str | None = None
    signing_context_digest: str | None = None
    standing_policy_digest: str | None = None
    maximum_confirmed_writes: int | None = None
    local_confirmed_writes: int | None = None
    local_readonly_reuses: int | None = None
    local_history_digest: str | None = None
    unclassified_terminal_rows: int | None = None
    observed_technical_schema: str = 'UNKNOWN'

    def diagnostic(self):
        return {**self.__dict__, 'schema_version':'native-common-completed-baseline-facts/v1',
            'budget_scope':'IMMUTABLE_PREPARED_BASELINE',
            'new_common_edit_needed':False if self.status == 'RETAINED_NATIVE_COMMON_BASELINE' else 'UNKNOWN',
            'new_common_human_approval_needed':False,
            'current_signed_readback':'UNKNOWN', 'edit_endpoint_permission':'UNKNOWN',
            'service_write_path_coverage':'UNKNOWN', 'schema_installation_receipt':'UNKNOWN',
            'provider_account_authority':'UNKNOWN', 'execution_authority':False}


class NativeCommonBaselineReader:
    def __init__(self, config, store):
        if type(config) is not StartupConfig or type(store) is not ReleaseStore:
            raise TypeError('COMMON_BASELINE_SERVICE_CONTEXT_REQUIRED')
        self._config, self._store = config, store

    def read(self, db, common_plan_id, common_run_id):
        """Re-read the completed source in one existing read-only snapshot.

        There is no provider request, reserve, consume, schema installation or
        fallback from a live rejection. A later freeze consumer must request a
        current signed READ separately, and must verify the native installation
        and write-path coverage receipt rather than infer them from these facts.
        """
        try:
            reader = NativeCommonSourceReader(self._store)
            reader.validate_context(db)
            if db.execute('PRAGMA query_only').fetchone()[0] != 1:
                raise ValueError('COMMON_BASELINE_READONLY_SNAPSHOT_REQUIRED')
            row = db.execute('SELECT * FROM release_plans WHERE plan_id=?', (common_plan_id,)).fetchone()
            if row is None:
                raise ValueError('COMMON_BASELINE_PLAN_MISSING')
            payload = json.loads(row['payload_json'])
            source_facts = reader.read_source_facts(db, common_plan_id)
            source = _validate_plan_source(db, payload)
            if not source or payload.get('targets') != ['miaoshou:COMMON']:
                raise ValueError('COMMON_BASELINE_PREPARATION_SOURCE_UNKNOWN')
            completion = read_retained_completion(reader, db, common_plan_id, common_run_id)
            census = census_same_snapshot(self._store, db, payload, source_facts)
            if census['status'] != 'LOCAL_PERSISTED_CENSUS' or census['unresolved_attempts']:
                raise ValueError('COMMON_BASELINE_ACTIVE_RESULT_REQUIRES_RECONCILIATION')
            signing = NativeCommonSigningContextReader(self._config, self._store).read(
                db, common_plan_id, common_run_id=common_run_id)
            if signing.status != 'PINNED_APP_AND_RETAINED_COMMON_READ_CONTEXT':
                raise ValueError('COMMON_BASELINE_SIGNING_CONTEXT_UNKNOWN')
            policy = inspect_service_policy(NativeCommonStandingPolicyReader(self._config))
            if policy.status != 'KNOWN_LOCAL_USER_INTENT' or policy.maximum_confirmed_writes != 1:
                raise ValueError('COMMON_BASELINE_STANDING_POLICY_UNKNOWN')
            # Exactly the confirmed write count, never attempts or reuse count.
            if census['local_observed_confirmed_writes'] != policy.maximum_confirmed_writes:
                raise ValueError('COMMON_BASELINE_CONFIRMED_COUNT_NOT_ESTABLISHED')
            if (completion.preparation_root_id != source['preparation_root']['root_id']
                    or signing.preparation_root_id != completion.preparation_root_id
                    or signing.retained_readback_digest != completion.readback_digest):
                raise ValueError('COMMON_BASELINE_RETAINED_CONTEXT_CHANGED')
            run = db.execute('SELECT technical_admission_json FROM release_runs WHERE run_id=? AND plan_id=?',
                (common_run_id, common_plan_id)).fetchone()
            mutation_sha256 = json.loads(run['technical_admission_json'])['mutation_sha256']
            history = [record.record_bytes.decode('utf-8') for record in source_facts.records]
            history_digest = hashlib.sha256(json.dumps(history, ensure_ascii=False,
                separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()
            # This observes exact table/trigger definitions, not an installation
            # attestation or the closure of all service write endpoints.
            observed_schema = 'MATCHING_NATIVE_TECHNICAL_SCHEMA' if _schema_installed(db) else 'UNKNOWN'
            return NativeCommonBaselineFacts('RETAINED_NATIVE_COMMON_BASELINE',
                offer_id=source['offer_id'], preparation_root_id=completion.preparation_root_id,
                completion_state=completion.state, mutation_sha256=mutation_sha256,
                retained_readback_digest=completion.readback_digest,
                signing_context_digest=signing.common_signing_context_digest,
                standing_policy_digest=policy.raw_digest, maximum_confirmed_writes=policy.maximum_confirmed_writes,
                local_confirmed_writes=census['local_observed_confirmed_writes'],
                local_readonly_reuses=census['local_observed_readonly_reuses'],
                local_history_digest=history_digest,
                unclassified_terminal_rows=len(census['unclassified_history']),
                observed_technical_schema=observed_schema)
        except (ValueError, TypeError, KeyError, AttributeError, OSError, RecursionError, ReleaseStoreError, sqlite3.Error) as error:
            reason = str(error) if str(error).startswith('COMMON_BASELINE_') else 'COMMON_BASELINE_SOURCE_UNVERIFIED'
            return NativeCommonBaselineFacts('UNKNOWN', reason)


def inspect_service_baseline(reader, db, common_plan_id, common_run_id):
    if type(reader) is not NativeCommonBaselineReader:
        return NativeCommonBaselineFacts('UNKNOWN', 'COMMON_BASELINE_SERVICE_READER_REQUIRED')
    return reader.read(db, common_plan_id, common_run_id)
