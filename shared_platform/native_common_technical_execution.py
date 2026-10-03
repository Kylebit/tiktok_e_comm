"""Durable COMMON technical mechanics in the existing ReleaseStore.

No human approval is synthesized. The public boundary reads actual service
configuration, prepared lineage, local path coverage and the installed schema.
Missing facts refuse reserve; EDIT acceptance is observed after durable UNKNOWN,
never asserted as a permission flag by the transaction helpers or caller.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
import json
import re
import sqlite3
import base64
import gzip
import io
from pathlib import Path

from shared_platform.release_store import ReleaseStore, ReleaseAuthorizationError, _utc_now
from shared_platform.native_common_budget_facts import _validate_plan_source, census_same_snapshot
from shared_platform.r3_common_source_facts import NativeCommonSourceReader

COMMON = 'miaoshou:COMMON'
SCHEMA = 'native-common-technical-execution/v1'
STATES = {'RESERVED', 'READONLY_RESERVED', 'UNKNOWN', 'CONFIRMED_WRITE', 'READONLY_REUSE', 'PROVEN_NOT_DISPATCHED'}


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def _fail(reason):
    raise ReleaseAuthorizationError(reason)


@dataclass(frozen=True)
class _TransactionFacts:
    """Internal transaction inputs, not proof or a public authority constructor.

    The service producer derives these inputs from its current SQLite snapshot.
    Owned core fixtures are not accepted as service registration or permission.
    """
    plan_id: str
    payload_digest: str
    offer_id: str
    account_identity_digest: str
    root_id: str
    mutation_bytes: bytes
    policy_digest: str
    confirmed_cap: int
    coverage_digest: str
    authority_digest: str
    native_account_context: dict | None = None

    def binding(self):
        value = {'schema_version': SCHEMA, 'plan_id': self.plan_id,
                'payload_digest': self.payload_digest, 'offer_id': self.offer_id,
                'account_identity_digest': self.account_identity_digest,
                'preparation_root_id': self.root_id, 'target_label': COMMON,
                'mutation_sha256': sha256(self.mutation_bytes).hexdigest(),
                'policy_digest': self.policy_digest, 'maximum_confirmed_writes': self.confirmed_cap,
                'coverage_digest': self.coverage_digest, 'authority_digest': self.authority_digest}
        if self.native_account_context is not None:
            value['native_account_context'] = self.native_account_context
        return value


def _schema_installed(db):
    rows = {row['name']: row for row in db.execute('PRAGMA table_info(release_runs)')}
    if (set(rows) != {'run_id','plan_id','approval_id','status','created_at','updated_at','completed_at',
                     'technical_admission_json','technical_admission_digest','technical_execution_state'}
            or rows['approval_id']['notnull'] != 0):
        return False
    actual = db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='release_runs'").fetchone()
    definition = (Path(__file__).parent/'migrations/native_common_technical_runs_v1.sql').read_text(encoding='utf-8')
    expected = definition.split('CREATE TABLE release_runs_native_v1',1)[1].split(';',1)[0]
    expected = 'CREATE TABLE release_runs' + expected
    normalize = lambda s: ''.join(s.replace('"release_runs"','release_runs').split()).casefold().replace('ifnotexists','')
    if not actual or normalize(actual['sql']) != normalize(expected):
        return False
    for name in ('trg_release_run_identity_immutable', 'release_runs_native_binding_immutable',
                 'release_runs_native_common_scope'):
        match = re.search(r'CREATE TRIGGER(?: IF NOT EXISTS)? '+name+r'\b.*?(?=\nCREATE TRIGGER|\nCOMMIT;)', definition, re.S)
        actual = db.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?", (name,)).fetchone()
        if not match or not actual or normalize(actual['sql'].rstrip(';')) != normalize(match[0].rstrip(';')):
            return False
    return True


@contextmanager
def _existing_transaction(store):
    if type(store) is not ReleaseStore or not store.path.is_file():
        _fail('COMMON_TECHNICAL_STORE_UNAVAILABLE')
    # mode=rw prevents a deletion/replacement race from creating an empty DB.
    db = sqlite3.connect(store.path.resolve().as_uri() + '?mode=rw', uri=True,
                         isolation_level=None, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA recursive_triggers=ON')
    db.execute('PRAGMA busy_timeout=30000')
    try:
        db.execute('BEGIN IMMEDIATE')
        if not _schema_installed(db):
            _fail('COMMON_TECHNICAL_SCHEMA_NOT_INSTALLED')
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def _service_facts(store, db, plan_id):
    """Read the existing plan/root; unavailable native grants fail before reserve."""
    row = db.execute('SELECT * FROM release_plans WHERE plan_id=?', (plan_id,)).fetchone()
    if row is None:
        _fail('COMMON_TECHNICAL_PLAN_MISSING')
    payload = json.loads(row['payload_json'])
    if payload.get('targets') != [COMMON]:
        _fail('COMMON_TECHNICAL_TARGET_SCOPE_INVALID')
    source = _validate_plan_source(db, payload)
    if not source or source['preparation_root'].get('status') != 'PERSISTED_NATIVE_PREPARATION_ROOT':
        _fail('COMMON_TECHNICAL_PREPARATION_ROOT_UNKNOWN')
    reader = NativeCommonSourceReader(store)
    observed = reader.read_source_facts(db, plan_id)
    census = census_same_snapshot(store, db, payload, observed)
    if census['status'] != 'LOCAL_PERSISTED_CENSUS':
        _fail('COMMON_HISTORICAL_WRITE_BUDGET_UNKNOWN')
    current = db.execute('SELECT run_id,approval_id,technical_execution_state FROM release_runs WHERE plan_id=?',
                         (plan_id,)).fetchone() if _schema_installed(db) else None
    own_reserved = (current['run_id'] if current is not None and current['approval_id'] is None
                    and current['technical_execution_state'] == 'RESERVED' else None)
    if any(record.get('run_id') != own_reserved for record in census['unresolved_attempts']) or any(
            row.get('same_preparation_root') is True for row in census['unclassified_history']):
        _fail('COMMON_PRIOR_ATTEMPT_RECONCILIATION_REQUIRED')
    from modules.products import server
    from shared_platform.publication_common_standing_policy import inspect_service_policy
    policy = inspect_service_policy(server._service_common_standing_policy_reader())
    if policy.status != 'KNOWN_LOCAL_USER_INTENT' or policy.maximum_confirmed_writes != 1:
        _fail('COMMON_STANDING_POLICY_AUTHORITY_UNKNOWN')
    from shared_platform import native_common_edit_boundary as boundary
    from modules.products.release_adapters import _immutable_miaoshou_common_draft
    active = boundary._ACTIVE
    signing_reader = server._service_common_signing_context_reader(store)
    policy_reader = server._service_common_standing_policy_reader()
    if (active is None or signing_reader is None
            or signing_reader._config is not active._config
            or policy_reader._config is not active._config):
        _fail('COMMON_ACCOUNT_AND_COMPLETE_COVERAGE_AUTHORITY_UNKNOWN')
    coverage = active.same_snapshot_coverage(store, db, plan_id)
    signing = signing_reader.read(db, plan_id)
    if signing.status != 'PINNED_COMMON_SIGNING_CONTEXT':
        _fail(signing.reason or 'COMMON_SIGNING_CONTEXT_UNVERIFIED')
    if (signing.offer_id != row['product_id']
            or signing.preparation_root_id != source['preparation_root']['root_id']
            or signing.r1_source_account_digest != source['account_identity_digest']):
        _fail('COMMON_SIGNING_PREPARATION_BINDING_CHANGED')
    # These are local configured signing identity and the user's standing
    # technical request authorization, not a declaration of EDIT permission.
    # Actual provider acceptance/rejection occurs after durable UNKNOWN.
    _, pinned = active._pinned_config(store)
    authority = {'schema_version':'native-common-service-request-context/v1',
        'preparation_root_id':signing.preparation_root_id,
        'r1_source_account_digest':signing.r1_source_account_digest,
        'common_signing_context_digest':signing.common_signing_context_digest,
        'config_raw_digest':pinned[0].hex(), 'standing_policy_digest':policy.raw_digest,
        'provider_edit_permission':'UNKNOWN', 'sole_human_gate':'FINAL_MARKETPLACE_PUBLISH'}
    return _TransactionFacts(plan_id, row['payload_digest'], row['product_id'],
        source['account_identity_digest'], source['preparation_root']['root_id'],
        _bytes(_immutable_miaoshou_common_draft(payload)), policy.raw_digest, 1,
        sha256(_bytes(coverage.diagnostic())).hexdigest(), sha256(_bytes(authority)).hexdigest(), authority)


def reserve(store, plan_id):
    """Service entry: no caller-provided root, cap, approval, or authority dict."""
    _preflight_source_readonly(store, plan_id)
    with _existing_transaction(store) as db:
        facts = _service_facts(store, db, plan_id)
        return _reserve_in_transaction(store, db, facts)


def _checked_facts(db, facts):
    if type(facts) is not _TransactionFacts or type(facts.confirmed_cap) is not int or facts.confirmed_cap != 1:
        _fail('COMMON_TECHNICAL_TRANSACTION_FACTS_INVALID')
    plan = db.execute('SELECT * FROM release_plans WHERE plan_id=?', (facts.plan_id,)).fetchone()
    if plan is None or plan['payload_digest'] != facts.payload_digest or plan['product_id'] != facts.offer_id:
        _fail('COMMON_TECHNICAL_PLAN_BINDING_CHANGED')
    payload = json.loads(plan['payload_json'])
    if payload.get('targets') != [COMMON]:
        _fail('COMMON_TECHNICAL_TARGET_SCOPE_INVALID')
    source = _validate_plan_source(db, payload)
    if (not source or source['preparation_root']['root_id'] != facts.root_id
            or source['account_identity_digest'] != facts.account_identity_digest):
        _fail('COMMON_TECHNICAL_PREPARATION_BINDING_CHANGED')
    from modules.products.release_adapters import _immutable_miaoshou_common_draft
    if facts.mutation_bytes != _bytes(_immutable_miaoshou_common_draft(payload)):
        _fail('COMMON_TECHNICAL_MUTATION_CHANGED')
    if any(type(v) is not str or not v for v in (facts.policy_digest, facts.coverage_digest, facts.authority_digest)):
        _fail('COMMON_TECHNICAL_AUTHORITY_SOURCE_MISSING')
    context = facts.native_account_context
    if context is not None:
        if (type(context) is not dict or set(context) != {'schema_version','preparation_root_id',
                'r1_source_account_digest','common_signing_context_digest','config_raw_digest',
                'standing_policy_digest','provider_edit_permission','sole_human_gate'}
                or context['schema_version'] != 'native-common-service-request-context/v1'
                or context['preparation_root_id'] != facts.root_id
                or context['r1_source_account_digest'] != facts.account_identity_digest
                or context['provider_edit_permission'] != 'UNKNOWN'
                or context['sole_human_gate'] != 'FINAL_MARKETPLACE_PUBLISH'
                or any(type(context[k]) is not str or not re.fullmatch('[0-9a-f]{64}',context[k])
                    for k in ('common_signing_context_digest','config_raw_digest','standing_policy_digest'))
                or sha256(_bytes(context)).hexdigest() != facts.authority_digest):
            _fail('COMMON_TECHNICAL_NATIVE_ACCOUNT_CONTEXT_CHANGED')
    return plan


def _native_runs(db, offer_id):
    return db.execute('SELECT r.* FROM release_runs r JOIN release_plans p ON p.plan_id=r.plan_id '
                      'WHERE p.product_id=?', (offer_id,)).fetchall()


def _reserve_in_transaction(store, db, facts, *, readonly_reuse=False):
    """Core mechanics only; callers must already hold the service snapshot."""
    _checked_facts(db, facts)
    encoded = _bytes(facts.binding()).decode()
    prior = db.execute('SELECT * FROM release_runs WHERE plan_id=?', (facts.plan_id,)).fetchone()
    if prior is not None:
        if prior['approval_id'] is not None or prior['technical_admission_json'] != encoded:
            _fail('COMMON_TECHNICAL_RUN_REPLAY_CONFLICT')
        if prior['technical_execution_state'] == 'PROVEN_NOT_DISPATCHED':
            # A proved zero-call failure does not spend a confirmed-write cap.
            observed = NativeCommonSourceReader(store).read_source_facts(db, facts.plan_id)
            payload = json.loads(_checked_facts(db, facts)['payload_json'])
            census = census_same_snapshot(store, db, payload, observed)
            if census['status'] != 'LOCAL_PERSISTED_CENSUS':
                _fail('COMMON_HISTORICAL_WRITE_BUDGET_UNKNOWN')
            if census['unresolved_attempts'] or any(
                    r['run_id'] != prior['run_id'] and r['approval_id'] is None
                    and r['technical_execution_state'] in {'RESERVED','UNKNOWN'}
                    for r in _native_runs(db, facts.offer_id)):
                _fail('COMMON_PRIOR_ATTEMPT_RECONCILIATION_REQUIRED')
            if census['local_observed_confirmed_writes'] >= facts.confirmed_cap:
                _fail('COMMON_CONFIRMED_WRITE_CAP_EXHAUSTED')
            now = _utc_now()
            changed = db.execute('UPDATE release_target_runs SET status=\'RUNNING\',attempts=attempts+1,'
                'error=NULL,completed_at=NULL,updated_at=? WHERE run_id=? AND target_label=? AND status=\'FAILED\'',
                (now, prior['run_id'], COMMON))
            if changed.rowcount != 1:
                _fail('COMMON_TECHNICAL_RESTART_CAS_LOST')
            db.execute('UPDATE release_runs SET technical_execution_state=\'RESERVED\',status=\'RUNNING\','
                       'completed_at=NULL,updated_at=? WHERE run_id=? AND technical_execution_state=\'PROVEN_NOT_DISPATCHED\'',
                       (now, prior['run_id']))
            prior = db.execute('SELECT * FROM release_runs WHERE run_id=?', (prior['run_id'],)).fetchone()
        return _receipt(prior, created=False)
    # Check active/unknown records across the Offer, including roots that cannot
    # be established on old human-bound rows. Terminal normalized history is
    # kept unclassified; it is not treated as an active request forever.
    plan = db.execute('SELECT payload_json FROM release_plans WHERE plan_id=?', (facts.plan_id,)).fetchone()
    observed = NativeCommonSourceReader(store).read_source_facts(db, facts.plan_id)
    census = census_same_snapshot(store, db, json.loads(plan['payload_json']), observed)
    if census['status'] != 'LOCAL_PERSISTED_CENSUS':
        _fail('COMMON_HISTORICAL_WRITE_BUDGET_UNKNOWN')
    if census['unresolved_attempts']:
        _fail('COMMON_PRIOR_ATTEMPT_RECONCILIATION_REQUIRED')
    confirmed = census['local_observed_confirmed_writes']
    reusable = False
    for run in _native_runs(db, facts.offer_id):
        if run['approval_id'] is not None:
            # Genuine coverage must account for old human-bound records; this
            # core never infers that legacy terminal evidence means zero writes.
            continue
        binding = _binding(run)
        state = run['technical_execution_state']
        if state in {'RESERVED', 'UNKNOWN'}:
            _fail('COMMON_PRIOR_ATTEMPT_RECONCILIATION_REQUIRED')
        if (binding['account_identity_digest'] == facts.account_identity_digest
                and binding['preparation_root_id'] == facts.root_id
                and state == 'CONFIRMED_WRITE'):
            # Census reads the same durable rows; do not add these twice.
            confirmed = max(confirmed, 1)
            if binding['mutation_sha256'] == facts.binding()['mutation_sha256']:
                reusable = True
    if readonly_reuse and not reusable:
        _fail('COMMON_TECHNICAL_REUSE_PREDECESSOR_MISSING')
    if not readonly_reuse and confirmed >= facts.confirmed_cap:
        _fail('COMMON_CONFIRMED_WRITE_CAP_EXHAUSTED')
    run_id = 'common-technical:' + sha256(encoded.encode()).hexdigest()[:32]
    now = _utc_now()
    db.execute('INSERT INTO release_runs '
               '(run_id,plan_id,approval_id,status,created_at,updated_at,technical_admission_json,'
               'technical_admission_digest,technical_execution_state) VALUES (?,?,NULL,\'RUNNING\',?,?,?,?,?)',
               (run_id, facts.plan_id, now, now, encoded, sha256(encoded.encode()).hexdigest(),
                'READONLY_RESERVED' if readonly_reuse else 'RESERVED'))
    db.execute('INSERT INTO release_target_runs '
               '(run_id,target_label,idempotency_key,status,attempts,created_at,updated_at) '
               'VALUES (?,?,?,\'RUNNING\',1,?,?)',
               (run_id, COMMON, sha256(_bytes([facts.root_id, facts.plan_id, COMMON])).hexdigest(), now, now))
    return _receipt(db.execute('SELECT * FROM release_runs WHERE run_id=?', (run_id,)).fetchone(), created=True)


def _binding(run):
    raw = run['technical_admission_json']
    if (run['approval_id'] is not None or type(raw) is not str
            or sha256(raw.encode()).hexdigest() != run['technical_admission_digest']):
        _fail('COMMON_TECHNICAL_RUN_CREDENTIAL_INVALID')
    value = json.loads(raw)
    if value.get('schema_version') != SCHEMA or value.get('target_label') != COMMON:
        _fail('COMMON_TECHNICAL_RUN_CREDENTIAL_INVALID')
    return value


def _receipt(run, *, created=False, consumed=False):
    return {'schema_version': SCHEMA, 'run_id': run['run_id'], 'state': run['technical_execution_state'],
            'binding': _binding(run), 'created': created, 'consumed': consumed,
            'external_writes_performed': [], 'execution_authority': False}


def _consume_in_transaction(db, facts, run_id):
    """Persist UNKNOWN before a transport can be scheduled, not after its reply."""
    _checked_facts(db, facts)
    run = db.execute('SELECT * FROM release_runs WHERE run_id=?', (run_id,)).fetchone()
    if run is None or _binding(run) != facts.binding():
        _fail('COMMON_TECHNICAL_CONSUME_BINDING_CHANGED')
    if run['technical_execution_state'] != 'RESERVED':
        return _receipt(run)
    now = _utc_now()
    changed = db.execute('UPDATE release_runs SET technical_execution_state=\'UNKNOWN\',updated_at=? '
                         'WHERE run_id=? AND technical_execution_state=\'RESERVED\' AND approval_id IS NULL', (now, run_id))
    if changed.rowcount != 1:
        _fail('COMMON_TECHNICAL_CONSUME_CAS_LOST')
    return _receipt(db.execute('SELECT * FROM release_runs WHERE run_id=?', (run_id,)).fetchone(), consumed=True)


def consume(store, plan_id, run_id):
    _preflight_source_readonly(store, plan_id)
    with _existing_transaction(store) as db:
        return _consume_in_transaction(db, _service_facts(store, db, plan_id), run_id)


def _preflight_source_readonly(store, plan_id):
    if type(store) is not ReleaseStore or not store.path.is_file():
        _fail('COMMON_TECHNICAL_STORE_UNAVAILABLE')
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        if not _schema_installed(db):
            _fail('COMMON_TECHNICAL_SCHEMA_NOT_INSTALLED')
        _service_facts(store, db, plan_id)


def _not_dispatched_in_transaction(db, facts, run_id):
    """Only RESERVED (not consumed) proves this component made zero calls."""
    _checked_facts(db, facts)
    run = db.execute('SELECT * FROM release_runs WHERE run_id=?', (run_id,)).fetchone()
    if run is None or _binding(run) != facts.binding() or run['technical_execution_state'] != 'RESERVED':
        _fail('COMMON_TECHNICAL_NOT_DISPATCHED_UNPROVEN')
    target = db.execute('SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?', (run_id, COMMON)).fetchone()
    if target is None or target['status'] != 'RUNNING' or target['attempts'] < 1:
        _fail('COMMON_TECHNICAL_NOT_DISPATCHED_UNPROVEN')
    evidence = {'schema_version': 'common-local-config-not-dispatched/v1',
        'source': SCHEMA, 'request_attempted': False, 'external_write_count': 0,
        'external_writes_performed': [], 'write_outcome': 'not_dispatched'}
    raw = _bytes(evidence).decode();now = _utc_now()
    db.execute('INSERT INTO release_target_failure_events VALUES (?,?,?,?,?,?)',
        (run_id, COMMON, target['attempts'], raw, sha256(raw.encode()).hexdigest(), now))
    db.execute('UPDATE release_target_runs SET status=\'FAILED\',updated_at=?,completed_at=? '
               'WHERE run_id=? AND target_label=?', (now, now, run_id, COMMON))
    db.execute('UPDATE release_runs SET technical_execution_state=\'PROVEN_NOT_DISPATCHED\',status=\'FAILED\','
               'updated_at=?,completed_at=? WHERE run_id=? AND technical_execution_state=\'RESERVED\'', (now, now, run_id))
    return _receipt(db.execute('SELECT * FROM release_runs WHERE run_id=?', (run_id,)).fetchone())


def _complete_retained_in_transaction(store, db, facts, run_id):
    """Recover only an exact, durably retained original comparison receipt.

    This local state transition is not a new provider observation or dispatch
    permission. The native service reserve path still requires installed grants.
    """
    plan = _checked_facts(db, facts)
    run = db.execute('SELECT * FROM release_runs WHERE run_id=?', (run_id,)).fetchone()
    if run is None or _binding(run) != facts.binding():
        _fail('COMMON_TECHNICAL_READBACK_BINDING_CHANGED')
    if run['technical_execution_state'] not in {'RESERVED', 'READONLY_RESERVED', 'UNKNOWN'}:
        return _receipt(run)
    target = dict(db.execute('SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?',
                            (run_id, COMMON)).fetchone())
    raw = db.execute('SELECT * FROM release_target_readbacks WHERE run_id=? AND target_label=?',
                     (run_id, COMMON)).fetchone()
    if raw is None:
        _fail('COMMON_TECHNICAL_RETAINED_READBACK_MISSING')
    evidence = json.loads(raw['evidence_json'])
    if sha256(raw['evidence_json'].encode()).hexdigest() != raw['evidence_digest']:
        _fail('COMMON_TECHNICAL_RETAINED_READBACK_CHANGED')
    if (not evidence.get('native_common_observation') or not evidence.get('stored_common_lineage')
            or store._validated_common_observation(db, target, evidence) != evidence):
        _fail('COMMON_TECHNICAL_RETAINED_READBACK_INVALID')
    from shared_platform.native_common_budget_facts import _READBACK_CHECKS
    checks = evidence.get('checks')
    if (evidence.get('source') not in {'miaoshou_open_api', 'miaoshou_common_readonly_detail'}
            or evidence.get('verified') is not True or type(checks) is not dict
            or not _READBACK_CHECKS.issubset(checks) or any(v is not True for v in checks.values())
            or evidence.get('offer_id') != facts.offer_id
            or evidence.get('image_count') != len(json.loads(plan['payload_json']).get('images') or [])
            or not raw['verified_at']
            or target['external_id'] not in (None, facts.offer_id)):
        _fail('COMMON_TECHNICAL_READBACK_COMPARISON_INVALID')
    write = evidence.get('external_writes_performed') == ['miaoshou:COMMON:immutable_plan_write']
    reuse = evidence.get('mode') == 'readback_reuse_no_write' and evidence.get('external_writes_performed') == []
    if write and run['technical_execution_state'] == 'UNKNOWN' and not reuse:
        if facts.native_account_context is not None:
            _check_readback_acceptance_reference(db, facts, run, target['attempts'], evidence)
        state = 'CONFIRMED_WRITE'
    elif reuse and run['technical_execution_state'] == 'READONLY_RESERVED':
        predecessor = evidence.get('predecessor')
        if type(predecessor) is not dict:
            _fail('COMMON_TECHNICAL_REUSE_PREDECESSOR_MISSING')
        old = db.execute('SELECT * FROM release_runs WHERE run_id=?', (predecessor.get('run_id'),)).fetchone()
        if (old is None or old['technical_execution_state'] != 'CONFIRMED_WRITE'
                or _binding(old)['mutation_sha256'] != facts.binding()['mutation_sha256']
                or _binding(old)['account_identity_digest'] != facts.account_identity_digest
                or _binding(old)['preparation_root_id'] != facts.root_id):
            _fail('COMMON_TECHNICAL_REUSE_PREDECESSOR_INVALID')
        old_readback = db.execute('SELECT * FROM release_target_readbacks WHERE run_id=? AND target_label=?',
                                 (old['run_id'], COMMON)).fetchone()
        old_plan = db.execute('SELECT * FROM release_plans WHERE plan_id=?', (old['plan_id'],)).fetchone()
        old_target = db.execute('SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?',
                                (old['run_id'], COMMON)).fetchone()
        if (old['plan_id'] != predecessor.get('plan_id') or old_readback is None
                or old_readback['evidence_digest'] != predecessor.get('common_readback_evidence_digest')
                or old_readback['verified_at'] != predecessor.get('common_readback_verified_at')
                or old_plan['payload_digest'] != predecessor.get('payload_digest')
                or old_target['status'] != predecessor.get('common_status') or old_target['status'] != 'SUCCEEDED'
                or not str(old_target['external_id']) == str(predecessor.get('common_external_id')) == facts.offer_id):
            _fail('COMMON_TECHNICAL_REUSE_PREDECESSOR_CHANGED')
        old_evidence = json.loads(old_readback['evidence_json'])
        if (sha256(old_readback['evidence_json'].encode()).hexdigest() != old_readback['evidence_digest']
                or store._validated_common_observation(db, dict(old_target), old_evidence) != old_evidence):
            _fail('COMMON_TECHNICAL_REUSE_PREDECESSOR_CHANGED')
        state = 'READONLY_REUSE'
    else:
        _fail('COMMON_TECHNICAL_READBACK_OUTCOME_UNKNOWN')
    now = _utc_now()
    changed = db.execute('UPDATE release_runs SET technical_execution_state=?,status=\'SUCCEEDED\',updated_at=?,'
                        'completed_at=? WHERE run_id=? AND technical_execution_state=?',
                        (state, now, now, run_id, run['technical_execution_state']))
    if changed.rowcount != 1:
        _fail('COMMON_TECHNICAL_RECOVERY_CAS_LOST')
    db.execute('UPDATE release_target_runs SET status=\'SUCCEEDED\',external_id=?,updated_at=?,completed_at=? '
               'WHERE run_id=? AND target_label=?', (facts.offer_id, now, now, run_id, COMMON))
    return _receipt(db.execute('SELECT * FROM release_runs WHERE run_id=?', (run_id,)).fetchone())


def inspect_existing(store, run_id):
    """Read durable UNKNOWN after restart without constructing a new attempt."""
    if type(store) is not ReleaseStore or not store.path.is_file():
        _fail('COMMON_TECHNICAL_STORE_UNAVAILABLE')
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        if not _schema_installed(db):
            _fail('COMMON_TECHNICAL_SCHEMA_NOT_INSTALLED')
        row = db.execute('SELECT * FROM release_runs WHERE run_id=?', (run_id,)).fetchone()
        if row is None:
            _fail('COMMON_TECHNICAL_RUN_MISSING')
        return _receipt(row)


def _facts_for_existing_run(db, plan_id, run_id):
    """Recover one existing credential for readback, never a fresh EDIT grant."""
    run = db.execute('SELECT * FROM release_runs WHERE run_id=? AND plan_id=?',
                     (run_id, plan_id)).fetchone()
    if run is None:
        _fail('COMMON_TECHNICAL_RUN_MISSING')
    binding = _binding(run)
    plan = db.execute('SELECT * FROM release_plans WHERE plan_id=?', (plan_id,)).fetchone()
    if plan is None:
        _fail('COMMON_TECHNICAL_PLAN_MISSING')
    from modules.products.release_adapters import _immutable_miaoshou_common_draft
    mutation = _bytes(_immutable_miaoshou_common_draft(json.loads(plan['payload_json'])))
    facts = _TransactionFacts(plan_id, plan['payload_digest'], plan['product_id'],
        binding['account_identity_digest'], binding['preparation_root_id'], mutation,
        binding['policy_digest'], binding['maximum_confirmed_writes'],
        binding['coverage_digest'], binding['authority_digest'], binding.get('native_account_context'))
    if facts.binding() != binding:
        _fail('COMMON_TECHNICAL_RUN_CREDENTIAL_CHANGED')
    _checked_facts(db, facts)
    return facts


def _validate_edit_wire(packet):
    from modules.miaoshou.client import OPEN_BASE_URL, COMMON_OBSERVATION_MAX_BYTES
    from shared_platform.native_common_edit_boundary import EDIT_PATH
    expected = {'schema_version','endpoint','http_status','credential_scope_digest','content_encoding',
        'request_base64','request_sha256','wire_base64','wire_sha256','business_base64','business_sha256'}
    try:
        if (type(packet) is not dict or set(packet) != expected
                or packet['schema_version'] != 'native-common-edit-wire/v1'
                or packet['endpoint'] != OPEN_BASE_URL + EDIT_PATH
                or type(packet['http_status']) is not int or packet['http_status'] != 200
                or packet['content_encoding'] not in {'identity','gzip'}
                or not re.fullmatch('[0-9a-f]{64}', packet['credential_scope_digest'])):
            raise ValueError('identity')
        decoded = {}
        for name in ('request','wire','business'):
            raw = packet[name+'_base64']
            if type(raw) is not str or len(raw) > (COMMON_OBSERVATION_MAX_BYTES+2)*4//3:
                raise ValueError('size')
            decoded[name] = base64.b64decode(raw, validate=True)
            if (len(decoded[name]) > COMMON_OBSERVATION_MAX_BYTES or
                    sha256(decoded[name]).hexdigest() != packet[name+'_sha256']):
                raise ValueError('bytes')
        if packet['content_encoding'] == 'gzip':
            with gzip.GzipFile(fileobj=io.BytesIO(decoded['wire'])) as stream:
                business = stream.read(COMMON_OBSERVATION_MAX_BYTES+1)
        else:
            business = decoded['wire']
        from shared_platform.native_common_signing_context import _unique_object
        response = json.loads(decoded['business'].decode('utf-8'), object_pairs_hook=_unique_object)
        body = json.loads(decoded['request'].decode('utf-8'), object_pairs_hook=_unique_object)
        if business != decoded['business'] or type(response) is not dict or response.get('result') != 'success':
            raise ValueError('acceptance')
        if type(body) is not dict:
            raise ValueError('request')
        return body
    except Exception:
        _fail('COMMON_TECHNICAL_EDIT_WIRE_INVALID')


def _retain_edit_acceptance(store, plan_id, run_id, attempt, body, packet):
    """Called only by the active service capture after its exact native POST."""
    from shared_platform.native_common_edit_boundary import native_edit_response_capture, EDIT_PATH
    capture = native_edit_response_capture(EDIT_PATH)
    if (capture is None or capture[1]._store is not store or capture[1]._plan_id != plan_id
            or capture[1]._native_claim != (run_id, attempt) or capture[2] != body):
        _fail('COMMON_TECHNICAL_EDIT_CAPTURE_REQUIRED')
    if _validate_edit_wire(packet) != body:
        _fail('COMMON_TECHNICAL_EDIT_REQUEST_CHANGED')
    with _existing_transaction(store) as db:
        facts = _facts_for_existing_run(db, plan_id, run_id)
        run = db.execute('SELECT * FROM release_runs WHERE run_id=?', (run_id,)).fetchone()
        target = db.execute('SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?',
                            (run_id, COMMON)).fetchone()
        context = facts.native_account_context
        if (run['technical_execution_state'] != 'UNKNOWN' or target['status'] != 'RUNNING'
                or type(attempt) is not int or target['attempts'] != attempt
                or context is None or packet['credential_scope_digest'] != context['common_signing_context_digest']
                or str(body.get('commonCollectBoxDetailId')) != facts.offer_id):
            _fail('COMMON_TECHNICAL_EDIT_CAPTURE_BINDING_CHANGED')
        _validate_accepted_request(db, facts, body)
        evidence = {'schema_version':'native-common-accepted-edit/v1',
            'run_id':run_id,'target_label':COMMON,'attempt':attempt,'binding':facts.binding(),
            'edit_wire':packet,'request_canonical_sha256':sha256(_bytes(body)).hexdigest(),
            'submission_accepted':True,'execution_authority':False}
        raw = _bytes(evidence).decode()
        previous = db.execute('SELECT * FROM release_target_submissions WHERE run_id=? AND target_label=?',
                              (run_id, COMMON)).fetchone()
        if previous is not None:
            if previous['evidence_json'] != raw or previous['evidence_digest'] != sha256(raw.encode()).hexdigest():
                _fail('COMMON_TECHNICAL_EDIT_ACCEPTANCE_REPLAY_CHANGED')
        else:
            db.execute('INSERT INTO release_target_submissions '
                '(run_id,target_label,external_id,evidence_json,evidence_digest,status,submitted_at) '
                'VALUES (?,?,?,?,?,\'SUBMITTED_UNVERIFIED\',?)',
                (run_id, COMMON, facts.offer_id, raw, sha256(raw.encode()).hexdigest(), _utc_now()))
        return sha256(raw.encode()).hexdigest()


def _validate_accepted_request(db, facts, body):
    """Rebuild immutable business fields; rehashed request JSON is not proof."""
    from modules.products.release_adapters import _build_immutable_common_edit, _immutable_miaoshou_common_draft
    if (type(body) is not dict or set(body) !=
            {'commonCollectBoxDetailId','editCommonCollectBoxDetail','ossMd5'}
            or type(body['commonCollectBoxDetailId']) is not int
            or str(body['commonCollectBoxDetailId']) != facts.offer_id
            or type(body['ossMd5']) is not str or not body['ossMd5'] or len(body['ossMd5']) > 4096
            or type(body['editCommonCollectBoxDetail']) is not dict):
        _fail('COMMON_TECHNICAL_ACCEPTED_REQUEST_SCHEMA_CHANGED')
    payload = json.loads(_checked_facts(db, facts)['payload_json'])
    if facts.mutation_bytes != _bytes(_immutable_miaoshou_common_draft(payload)):
        _fail('COMMON_TECHNICAL_MUTATION_CHANGED')
    try:
        rebuilt = _build_immutable_common_edit(payload, body['editCommonCollectBoxDetail'])
    except (ValueError, TypeError, KeyError, RuntimeError):
        _fail('COMMON_TECHNICAL_ACCEPTED_MUTATION_CHANGED')
    if _bytes(rebuilt) != _bytes(body['editCommonCollectBoxDetail']):
        _fail('COMMON_TECHNICAL_ACCEPTED_MUTATION_CHANGED')


def _accepted_edit_in_transaction(db, facts, run, attempt):
    """Original acceptance is necessary; a later matching READ is insufficient."""
    row = db.execute('SELECT * FROM release_target_submissions WHERE run_id=? AND target_label=?',
                     (run['run_id'], COMMON)).fetchone()
    if row is None:
        _fail('COMMON_TECHNICAL_ACCEPTED_EDIT_RECEIPT_MISSING')
    try:
        value = json.loads(row['evidence_json'])
        if (sha256(row['evidence_json'].encode()).hexdigest() != row['evidence_digest']
                or set(value) != {'schema_version','run_id','target_label','attempt','binding','edit_wire',
                                  'request_canonical_sha256','submission_accepted','execution_authority'}
                or value['schema_version'] != 'native-common-accepted-edit/v1'
                or value['run_id'] != run['run_id'] or value['target_label'] != COMMON
                or type(value['attempt']) is not int or value['attempt'] != attempt
                or value['binding'] != facts.binding() or value['submission_accepted'] is not True
                or value['execution_authority'] is not False or row['external_id'] != facts.offer_id
                or row['status'] != 'SUBMITTED_UNVERIFIED' or facts.native_account_context is None
                or value['edit_wire']['credential_scope_digest'] !=
                   facts.native_account_context['common_signing_context_digest']):
            raise ValueError('binding')
        body = _validate_edit_wire(value['edit_wire'])
        if (sha256(_bytes(body)).hexdigest() != value['request_canonical_sha256']
                or str(body.get('commonCollectBoxDetailId')) != facts.offer_id):
            raise ValueError('request')
        _validate_accepted_request(db, facts, body)
        return value
    except ReleaseAuthorizationError:
        raise
    except Exception:
        _fail('COMMON_TECHNICAL_ACCEPTED_EDIT_RECEIPT_CHANGED')


def _check_readback_acceptance_reference(db, facts, run, attempt, evidence):
    _accepted_edit_in_transaction(db, facts, run, attempt)
    row = db.execute('SELECT evidence_digest FROM release_target_submissions WHERE run_id=? AND target_label=?',
                     (run['run_id'], COMMON)).fetchone()
    if evidence.get('prior_external_write_evidence_digest') != row['evidence_digest']:
        _fail('COMMON_TECHNICAL_READBACK_ACCEPTANCE_REFERENCE_CHANGED')
    return row['evidence_digest']


def accepted_edit_for_recovery(store, db, plan_id, run_id):
    """Same snapshot read guard, never reserve/consume or acquire an EDIT grant."""
    facts = _facts_for_existing_run(db, plan_id, run_id)
    run = db.execute('SELECT * FROM release_runs WHERE run_id=?', (run_id,)).fetchone()
    target = db.execute('SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?',
                        (run_id, COMMON)).fetchone()
    if run['technical_execution_state'] != 'UNKNOWN' or target['status'] != 'RUNNING':
        _fail('COMMON_TECHNICAL_RECOVERY_STATE_CHANGED')
    accepted = _accepted_edit_in_transaction(db, facts, run, target['attempts'])
    from modules.products import server
    reader = server._service_common_signing_context_reader(store)
    if reader is None:
        _fail('COMMON_SIGNING_CURRENT_CONTEXT_UNAVAILABLE')
    signing = reader.read(db, plan_id)
    if (signing.status != 'PINNED_COMMON_SIGNING_CONTEXT'
            or signing.preparation_root_id != facts.root_id or signing.offer_id != facts.offer_id
            or signing.common_signing_context_digest != accepted['edit_wire']['credential_scope_digest']):
        _fail('COMMON_SIGNING_CURRENT_CONTEXT_CHANGED')
    return {'run_id':run_id,'target_attempt':target['attempts'],
            'accepted_edit_digest':db.execute('SELECT evidence_digest FROM release_target_submissions '
                'WHERE run_id=? AND target_label=?',(run_id,COMMON)).fetchone()['evidence_digest'],
            'read_signing_context_digest':accepted['edit_wire']['credential_scope_digest']}


def _retain_in_transaction(store, db, plan_id, run_id, attempt, evidence):
    """Atomically retain original READ bytes and close that same native attempt."""
    facts = _facts_for_existing_run(db, plan_id, run_id)
    run = db.execute('SELECT * FROM release_runs WHERE run_id=?', (run_id,)).fetchone()
    target = db.execute('SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?',
                        (run_id, COMMON)).fetchone()
    if (type(attempt) is not int or attempt < 1 or target is None
            or target['attempts'] != attempt
            or run['technical_execution_state'] not in
                {'UNKNOWN', 'READONLY_RESERVED', 'CONFIRMED_WRITE', 'READONLY_REUSE'}
            or target['status'] not in {'RUNNING', 'SUCCEEDED'}):
        _fail('COMMON_TECHNICAL_READBACK_ATTEMPT_CHANGED')
    checked = store._validated_common_observation(db, dict(target), evidence)
    if (facts.native_account_context is not None and
            (checked.get('native_common_observation') or {}).get('credential_scope_digest')
                != facts.native_account_context['common_signing_context_digest']):
        _fail('COMMON_TECHNICAL_READBACK_SIGNING_CONTEXT_CHANGED')
    raw = _bytes(checked).decode()
    previous = db.execute('SELECT * FROM release_target_readbacks WHERE run_id=? AND target_label=?',
                          (run_id, COMMON)).fetchone()
    if previous is not None:
        if (previous['evidence_json'] != raw
                or previous['evidence_digest'] != sha256(raw.encode()).hexdigest()):
            _fail('COMMON_TECHNICAL_READBACK_REPLAY_CHANGED')
    else:
        if run['technical_execution_state'] not in {'UNKNOWN', 'READONLY_RESERVED'}:
            _fail('COMMON_TECHNICAL_RETAINED_READBACK_MISSING')
        db.execute('INSERT INTO release_target_readbacks VALUES (?,?,?,?,?)',
                   (run_id, COMMON, raw, sha256(raw.encode()).hexdigest(), _utc_now()))
    # Original packet/16+extra checks and seven-field reuse predecessor remain
    # the final transition guard. Any rejection rolls back the inserted row.
    result = _complete_retained_in_transaction(store, db, facts, run_id)
    # An idempotent completed run still rechecks its stored comparison; a
    # terminal state is not permission to trust changed or false evidence.
    from shared_platform.native_common_retained_completion import read_retained_completion
    read_retained_completion(NativeCommonSourceReader(store), db, plan_id, run_id)
    return result


def retain_readback(store, plan_id, run_id, attempt, evidence):
    """No reserve, new attempt, approval, or dispatch is implied by retention."""
    with _existing_transaction(store) as db:
        return _retain_in_transaction(store, db, plan_id, run_id, attempt, evidence)


def retain_read_failure(store, plan_id, run_id, attempt, reason):
    """Keep accepted EDIT and unproved READ separate without confirming budget."""
    if reason != 'COMMON_ACCEPTED_EDIT_READBACK_UNPROVEN':
        _fail('COMMON_TECHNICAL_READ_FAILURE_REASON_INVALID')
    with _existing_transaction(store) as db:
        facts = _facts_for_existing_run(db, plan_id, run_id)
        run = db.execute('SELECT * FROM release_runs WHERE run_id=?', (run_id,)).fetchone()
        target = db.execute('SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?',
                            (run_id, COMMON)).fetchone()
        if (run['technical_execution_state'] != 'UNKNOWN' or type(attempt) is not int
                or target['attempts'] != attempt):
            _fail('COMMON_TECHNICAL_READ_FAILURE_ATTEMPT_CHANGED')
        _accepted_edit_in_transaction(db, facts, run, attempt)
        value = {'schema_version':'native-common-accepted-edit-read-failure/v1',
            'run_id':run_id,'attempt':attempt,'reason':reason,'edit_acceptance_retained':True,
            'readback_verified':False,'confirmed_write_count':'UNKNOWN',
            'external_writes_performed':[],'execution_authority':False}
        raw = _bytes(value).decode()
        db.execute('INSERT OR IGNORE INTO release_target_failure_events VALUES (?,?,?,?,?,?)',
                   (run_id, COMMON, attempt, raw, sha256(raw.encode()).hexdigest(), _utc_now()))


def _reuse_facts_in_transaction(store, db, plan_id):
    """An exact completed baseline can be read again without a new EDIT grant."""
    from modules.products import server
    from shared_platform.publication_common_standing_policy import inspect_service_policy
    from shared_platform.native_common_retained_completion import read_retained_completion
    from modules.products.release_adapters import _immutable_miaoshou_common_draft
    plan = db.execute('SELECT * FROM release_plans WHERE plan_id=?', (plan_id,)).fetchone()
    if plan is None:
        _fail('COMMON_TECHNICAL_PLAN_MISSING')
    payload = json.loads(plan['payload_json'])
    source = _validate_plan_source(db, payload)
    if not source or payload.get('targets') != [COMMON]:
        _fail('COMMON_TECHNICAL_PREPARATION_ROOT_UNKNOWN')
    policy = inspect_service_policy(server._service_common_standing_policy_reader())
    if policy.status != 'KNOWN_LOCAL_USER_INTENT' or policy.maximum_confirmed_writes != 1:
        _fail('COMMON_STANDING_POLICY_AUTHORITY_UNKNOWN')
    reader = NativeCommonSourceReader(store)
    observed = reader.read_source_facts(db, plan_id)
    census = census_same_snapshot(store, db, payload, observed)
    if (census['status'] != 'LOCAL_PERSISTED_CENSUS' or census['unresolved_attempts']
            or census['local_observed_confirmed_writes'] != 1):
        _fail('COMMON_PRIOR_ATTEMPT_RECONCILIATION_REQUIRED')
    mutation = _bytes(_immutable_miaoshou_common_draft(payload))
    signing_reader = server._service_common_signing_context_reader(store)
    if signing_reader is None:
        _fail('COMMON_SIGNING_SERVICE_READER_REQUIRED')
    matching = []
    for run in _native_runs(db, plan['product_id']):
        if run['approval_id'] is not None:
            continue
        if run['technical_execution_state'] in {'RESERVED', 'UNKNOWN'}:
            _fail('COMMON_PRIOR_ATTEMPT_RECONCILIATION_REQUIRED')
        if run['technical_execution_state'] != 'CONFIRMED_WRITE':
            continue
        old = _facts_for_existing_run(db, run['plan_id'], run['run_id'])
        if (old.root_id != source['preparation_root']['root_id']
                or old.account_identity_digest != source['account_identity_digest']
                or old.mutation_bytes != mutation):
            continue
        completion = read_retained_completion(reader, db, run['plan_id'], run['run_id'])
        signing = signing_reader.read(db, run['plan_id'], common_run_id=run['run_id'])
        if (signing.status != 'PINNED_APP_AND_RETAINED_COMMON_READ_CONTEXT'
                or signing.preparation_root_id != completion.preparation_root_id
                or signing.retained_readback_digest != completion.readback_digest):
            _fail('COMMON_SIGNING_RETAINED_CONTEXT_CHANGED')
        readback = db.execute('SELECT * FROM release_target_readbacks WHERE run_id=? AND target_label=?',
                             (run['run_id'], COMMON)).fetchone()
        target = db.execute('SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?',
                           (run['run_id'], COMMON)).fetchone()
        predecessor = {'plan_id':run['plan_id'], 'run_id':run['run_id'],
            'payload_digest':old.payload_digest, 'common_external_id':target['external_id'],
            'common_status':target['status'], 'common_readback_evidence_digest':readback['evidence_digest'],
            'common_readback_verified_at':readback['verified_at']}
        facts = _TransactionFacts(plan_id, plan['payload_digest'], plan['product_id'],
            old.account_identity_digest, old.root_id, mutation, policy.raw_digest, 1,
            old.coverage_digest, old.authority_digest, old.native_account_context)
        _checked_facts(db, facts)
        matching.append((facts, predecessor, signing.common_signing_context_digest))
    if len(matching) != 1:
        _fail('COMMON_TECHNICAL_REUSE_PREDECESSOR_MISSING_OR_AMBIGUOUS')
    return matching[0]


def reserve_completed_reuse(store, plan_id):
    """Service-only READ continuation; no caller cap/root/credential input."""
    if type(store) is not ReleaseStore or not store.path.is_file():
        _fail('COMMON_TECHNICAL_STORE_UNAVAILABLE')
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        if not _schema_installed(db):
            _fail('COMMON_TECHNICAL_SCHEMA_NOT_INSTALLED')
        _reuse_facts_in_transaction(store, db, plan_id)
    with _existing_transaction(store) as db:
        facts, predecessor, app_scope = _reuse_facts_in_transaction(store, db, plan_id)
        reserved = _reserve_in_transaction(store, db, facts, readonly_reuse=True)
        target = db.execute('SELECT attempts FROM release_target_runs WHERE run_id=? AND target_label=?',
                            (reserved['run_id'], COMMON)).fetchone()
        return {**reserved, 'target_attempt':target['attempts'],
                'predecessor':predecessor, 'read_signing_context_digest':app_scope}
