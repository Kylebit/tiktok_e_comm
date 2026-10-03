"""Same-store technical completion observations, never execution authority.

The durable state and retained transport/field comparison are rechecked inside
the caller's existing read transaction. This does not establish provider grants,
complete external history, installed migration authority, or an operator decision.
"""
from dataclasses import dataclass
from hashlib import sha256
import json

from shared_platform.r3_frozen_review_producer import DomainReviewBlocked


@dataclass(frozen=True)
class RetainedCommonCompletion:
    plan_id: str
    payload_digest: str
    offer_id: str
    run_id: str
    readback_digest: str
    state: str
    preparation_root_id: str
    account_identity_digest: str


def read_retained_completion(reader, db, common_plan_id, common_run_id):
    """Read genuine durable lineage; caller dictionaries cannot replace storage."""
    from shared_platform.r3_common_source_facts import NativeCommonSourceReader
    from shared_platform import native_common_technical_execution as technical
    from shared_platform.native_common_budget_facts import _READBACK_CHECKS
    from modules.products.release_adapters import _immutable_miaoshou_common_draft
    if type(reader) is not NativeCommonSourceReader:
        raise DomainReviewBlocked('COMMON_NATIVE_SOURCE_READER_REQUIRED')
    reader.validate_context(db)
    if not technical._schema_installed(db):
        raise DomainReviewBlocked('COMMON_TECHNICAL_SCHEMA_NOT_INSTALLED')
    plan = db.execute('SELECT * FROM release_plans WHERE plan_id=?', (common_plan_id,)).fetchone()
    run = db.execute('SELECT * FROM release_runs WHERE run_id=? AND plan_id=?',
                     (common_run_id, common_plan_id)).fetchone()
    if plan is None or run is None:
        raise DomainReviewBlocked('COMMON_TECHNICAL_COMPLETION_MISSING')
    binding = technical._binding(run)
    payload = json.loads(plan['payload_json'])
    facts = technical._TransactionFacts(common_plan_id, plan['payload_digest'], plan['product_id'],
        binding.get('account_identity_digest'), binding.get('preparation_root_id'),
        technical._bytes(_immutable_miaoshou_common_draft(payload)), binding.get('policy_digest'),
        binding.get('maximum_confirmed_writes'), binding.get('coverage_digest'), binding.get('authority_digest'),
        binding.get('native_account_context'))
    technical._checked_facts(db, facts)
    # Validate exact COMMON scope and durable plan/run/prepared binding before
    # following any graph reference. A market plan cannot recurse as COMMON.
    reader.read_source_facts(db, common_plan_id)
    encoded = technical._bytes(binding)
    if (binding != facts.binding() or encoded.decode() != run['technical_admission_json']
            or plan['payload_json'].encode() != technical._bytes(payload)
            or sha256(plan['payload_json'].encode()).hexdigest() != plan['payload_digest']
            or run['status'] != 'SUCCEEDED'
            or run['technical_execution_state'] not in {'CONFIRMED_WRITE', 'READONLY_REUSE'}):
        raise DomainReviewBlocked('COMMON_TECHNICAL_COMPLETION_UNPROVEN')
    targets = db.execute('SELECT * FROM release_target_runs WHERE run_id=?', (common_run_id,)).fetchall()
    if (len(targets) != 1 or targets[0]['target_label'] != technical.COMMON
            or targets[0]['status'] != 'SUCCEEDED' or targets[0]['external_id'] != facts.offer_id):
        raise DomainReviewBlocked('COMMON_TECHNICAL_COMPLETION_TARGET_CHANGED')
    target = dict(targets[0])
    readback = db.execute('SELECT * FROM release_target_readbacks WHERE run_id=? AND target_label=?',
                          (common_run_id, technical.COMMON)).fetchone()
    if readback is None:
        raise DomainReviewBlocked('COMMON_TECHNICAL_RETAINED_READBACK_MISSING')
    raw = readback['evidence_json'].encode()
    evidence = json.loads(raw)
    checks = evidence.get('checks')
    if (sha256(raw).hexdigest() != readback['evidence_digest'] or technical._bytes(evidence) != raw
            or not evidence.get('native_common_observation') or not evidence.get('stored_common_lineage')
            or reader.store._validated_common_observation(db, target, evidence) != evidence
            or evidence.get('source') not in {'miaoshou_open_api', 'miaoshou_common_readonly_detail'}
            or evidence.get('verified') is not True or type(checks) is not dict
            or not _READBACK_CHECKS.issubset(checks) or any(v is not True for v in checks.values())
            or evidence.get('offer_id') != facts.offer_id
            or evidence.get('image_count') != len(payload.get('images') or []) or not readback['verified_at']):
        raise DomainReviewBlocked('COMMON_TECHNICAL_RETAINED_COMPARISON_CHANGED')
    if (facts.native_account_context is not None and
            evidence['native_common_observation']['credential_scope_digest']
                != facts.native_account_context['common_signing_context_digest']):
        raise DomainReviewBlocked('COMMON_TECHNICAL_READBACK_SIGNING_CONTEXT_CHANGED')
    state = run['technical_execution_state']
    if state == 'CONFIRMED_WRITE':
        if (evidence.get('external_writes_performed') != ['miaoshou:COMMON:immutable_plan_write']
                or evidence.get('mode') == 'readback_reuse_no_write'):
            raise DomainReviewBlocked('COMMON_TECHNICAL_WRITE_OUTCOME_UNPROVEN')
        if facts.native_account_context is not None:
            technical._check_readback_acceptance_reference(db, facts, run, target['attempts'], evidence)
    else:
        predecessor = evidence.get('predecessor')
        if (evidence.get('mode') != 'readback_reuse_no_write'
                or evidence.get('external_writes_performed') != [] or type(predecessor) is not dict
                or set(predecessor) != {'plan_id', 'run_id', 'payload_digest', 'common_status',
                    'common_external_id', 'common_readback_evidence_digest', 'common_readback_verified_at'}
                or predecessor.get('run_id') == common_run_id):
            raise DomainReviewBlocked('COMMON_TECHNICAL_REUSE_PREDECESSOR_INVALID')
        old_run = db.execute('SELECT * FROM release_runs WHERE run_id=?', (predecessor['run_id'],)).fetchone()
        # Require a confirmed write before recursion; a reused chain cannot cycle.
        if old_run is None or old_run['technical_execution_state'] != 'CONFIRMED_WRITE':
            raise DomainReviewBlocked('COMMON_TECHNICAL_REUSE_PREDECESSOR_INVALID')
        old = read_retained_completion(reader, db, predecessor['plan_id'], predecessor['run_id'])
        old_binding = technical._binding(old_run)
        old_readback = db.execute('SELECT verified_at FROM release_target_readbacks WHERE run_id=? AND target_label=?',
                                  (old.run_id, technical.COMMON)).fetchone()
        if (old.state != 'CONFIRMED_WRITE' or old.payload_digest != predecessor['payload_digest']
                or old.readback_digest != predecessor['common_readback_evidence_digest']
                or old_readback['verified_at'] != predecessor['common_readback_verified_at']
                or predecessor['common_status'] != 'SUCCEEDED' or predecessor['common_external_id'] != facts.offer_id
                or old.offer_id != facts.offer_id or old.account_identity_digest != facts.account_identity_digest
                or old.preparation_root_id != facts.root_id
                or old_binding['mutation_sha256'] != binding['mutation_sha256']):
            raise DomainReviewBlocked('COMMON_TECHNICAL_REUSE_PREDECESSOR_CHANGED')
    return RetainedCommonCompletion(common_plan_id, plan['payload_digest'], facts.offer_id,
        common_run_id, readback['evidence_digest'], state, facts.root_id, facts.account_identity_digest)
