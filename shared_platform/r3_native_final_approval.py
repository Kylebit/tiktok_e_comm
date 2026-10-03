"""Native sole-decision bridge. Real source admission is default closed.

The explicit fixture actor is native, owner-bound and SYNTHETIC_TEST_ONLY.
It is not an installed production COMMON proof or a provider authority.
"""
from __future__ import annotations

import json
from dataclasses import asdict

from shared_platform.common_offer_authority_store import MODE, canonical_bytes, digest
from shared_platform.final_review_server_admission import ApprovalBlocked
from shared_platform.local_operator_session import verify_owner_only
from shared_platform.private_final_decision_store import PrivateFinalDecisionStore


PROFILE_SCHEMA = 'native-actor-profile-binding/v1'
BINDING_SCHEMA = 'native-sole-decision-binding/v1'


def read_native_actor_identity(actor_reader):
    """Read an installed native owner adapter; this does not admit a candidate.

    The native decision service may inject this exact reader after trusted
    local startup. COMMON/official-readback/budget and frozen-candidate guards
    remain independent; neither private fixture actor nor request dict is used.
    """
    from shared_platform.native_windows_actor import NativeWindowsActorProfileReader
    from shared_platform.native_actor_authentication import NativeHelperActorProfileReader
    if type(actor_reader) not in (NativeWindowsActorProfileReader,NativeHelperActorProfileReader):
        raise ApprovalBlocked('NATIVE_ACTOR_SERVICE_READER_REQUIRED')
    return actor_reader.read_verified()


class PrivateFixtureActorProfileReader:
    """Exact service-owned fixture binding; no actor or mapping request fields."""
    def __init__(self, decisions):
        if type(decisions) is not PrivateFinalDecisionStore:
            raise ApprovalBlocked('NATIVE_ACTOR_SERVICE_READER_REQUIRED')
        self.decisions = decisions
        self.path = decisions.sessions.root / 'native-actor-profile.json'

    def read_verified(self):
        try:
            sid = self.decisions.sessions._owner()
            verify_owner_only(self.path, sid, protected=False)
            raw = self.path.read_bytes()
            value = json.loads(raw)
            expected = {'schema_version': PROFILE_SCHEMA, 'owner_sid': sid,
                'logical_profile': 'Kyle', 'mapping_source': 'EXPLICIT_PRIVATE_FIXTURE_BOOTSTRAP',
                'instance_id': self.decisions.authority.marker['instance_id'],
                'evidence_kind': MODE, 'execution_authority': False}
            if value != expected or raw != canonical_bytes(expected):
                raise ValueError('binding mismatch')
        except Exception:
            raise ApprovalBlocked('NATIVE_ACTOR_PROFILE_BINDING_INVALID') from None
        return {**value, 'mapping_digest': digest(raw)}


def initialize_private_fixture_actor_profile(decisions):
    """Explicit native fixture setup, never HTTP or production installation."""
    reader = PrivateFixtureActorProfileReader(decisions)
    sid = decisions.sessions._owner()
    value = {'schema_version': PROFILE_SCHEMA, 'owner_sid': sid,
        'logical_profile': 'Kyle', 'mapping_source': 'EXPLICIT_PRIVATE_FIXTURE_BOOTSTRAP',
        'instance_id': decisions.authority.marker['instance_id'],
        'evidence_kind': MODE, 'execution_authority': False}
    with reader.path.open('xb') as stream:
        stream.write(canonical_bytes(value))
    reader.read_verified()
    return reader


class NativeSoleDecisionConsumer:
    def __init__(self, decisions=None, *, actor_reader=None):
        self.decisions = decisions
        self.actor_reader = actor_reader

    def decide(self, grant, *, nonce, review_digest):
        if (type(self.decisions) is not PrivateFinalDecisionStore
                or type(self.actor_reader) is not PrivateFixtureActorProfileReader
                or self.actor_reader.decisions is not self.decisions):
            raise ApprovalBlocked('COMMON_TRUSTED_BUDGET_AND_OFFICIAL_READBACK_REQUIRED')
        self.actor_reader.read_verified()
        if type(nonce) is not str or not nonce or type(review_digest) is not str or not review_digest:
            raise ApprovalBlocked('NATIVE_DECISION_FIELDS_INVALID')
        with self.decisions._transaction() as db:
            return self.decisions._decide_in_transaction(db, grant, nonce=nonce,
                review_digest=review_digest, native_consumer=self)

    def _write_binding_in_transaction(self, db, review, owner_sid, decision_id):
        from shared_platform.release_store import _plan_from_row
        actor = self.actor_reader.read_verified()
        if actor['owner_sid'] != owner_sid or not db.in_transaction:
            raise ApprovalBlocked('NATIVE_ACTOR_TRANSACTION_BINDING_INVALID')
        # _candidate has rebuilt the actual full graph and synthetic COMMON
        # proof in this same connection before the native nonce can be consumed.
        current, _, _ = self.decisions._candidate(db, review.digest())
        if current['review_digest'] != review.digest():
            raise ApprovalBlocked('NATIVE_FROZEN_REVIEW_CHANGED')
        plan = db.execute('SELECT * FROM release_plans WHERE plan_id=?', (review.plan_id,)).fetchone()
        if not plan or plan['product_id'] != review.offer_id or plan['status'] != 'PENDING_APPROVAL':
            raise ApprovalBlocked('NATIVE_MARKETPLACE_PENDING_PLAN_REQUIRED')
        payload = _plan_from_row(plan)['payload']
        if (not payload.get('r3_marketplace_binding')
                or payload.get('approved_publication_snapshot_schema_version') != 'approved-publication-snapshot/v4'
                or tuple(json.loads(plan['target_labels_json'])) != review.targets
                or db.execute('SELECT 1 FROM release_approvals WHERE plan_id=?', (review.plan_id,)).fetchone()):
            raise ApprovalBlocked('NATIVE_COMPLETE_V4_PLAN_REQUIRED')
        result = self.decisions.authority.store._persist_approved_plan_in_transaction(
            db, plan, logical_profile=actor['logical_profile'])
        if not result.get('publication_snapshot'):
            raise ApprovalBlocked('NATIVE_V4_SNAPSHOT_REQUIRED')
        binding = {'schema_version': BINDING_SCHEMA, 'review': asdict(review),
            'actor': actor, 'execution_authority': False,
            'decision_id': decision_id, 'review_digest': review.digest(),
            'release_approval_id': result['approval_id'],
            'publication_snapshot_digest': result['publication_snapshot']['snapshot_digest']}
        return canonical_bytes(binding)


def _checked_native_envelope(raw, decision, frozen):
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        raise ApprovalBlocked('NATIVE_APPROVAL_ENVELOPE_INVALID') from None
    if (type(value) is not dict or set(value) != {'schema_version','review','actor',
            'execution_authority','release_approval_id','publication_snapshot_digest',
            'decision_id','review_digest'}
            or value['schema_version'] != BINDING_SCHEMA or value['execution_authority'] is not False
            or canonical_bytes(value) != raw or canonical_bytes(value['review']) != canonical_bytes(asdict(frozen))
            or not decision or decision['evidence_kind'] != MODE
            or decision['review_digest'] != frozen.digest()
            or value['review_digest'] != frozen.digest()
            or value['decision_id'] != decision['decision_id']):
        raise ApprovalBlocked('NATIVE_APPROVAL_ENVELOPE_INVALID')
    actor = value['actor']
    if (type(actor) is not dict or set(actor) != {'schema_version','owner_sid','logical_profile',
            'mapping_source','instance_id','evidence_kind','execution_authority','mapping_digest'}
            or actor['schema_version'] != PROFILE_SCHEMA or actor['logical_profile'] != 'Kyle'
            or actor['owner_sid'] != decision['owner_sid'] or not actor['owner_sid']
            or actor['mapping_source'] != 'EXPLICIT_PRIVATE_FIXTURE_BOOTSTRAP'
            or actor['evidence_kind'] != MODE or actor['execution_authority'] is not False
            or not actor['instance_id']
            or actor['mapping_digest'] != digest(canonical_bytes({k:v for k,v in actor.items() if k!='mapping_digest'}))):
        raise ApprovalBlocked('NATIVE_APPROVAL_ACTOR_BINDING_INVALID')
    return value


def read_native_approval_binding(db, approval, decision, frozen):
    """Read the new version without changing bare FrozenReview old contracts."""
    from shared_platform.release_store import _plan_from_row, _validated_publication_snapshot_row, ImmutableReleaseError
    if (not approval or not decision or approval['review_digest'] != frozen.digest()
            or decision['review_digest'] != frozen.digest()
            or approval['decision_id'] != decision['decision_id']):
        raise ApprovalBlocked('NATIVE_APPROVAL_BYTES_INVALID')
    raw = approval['domain_binding_json'].encode()
    if digest(raw) != approval['domain_binding_sha256']:
        raise ApprovalBlocked('NATIVE_APPROVAL_BYTES_INVALID')
    value = _checked_native_envelope(raw, decision, frozen)
    actor = value['actor']
    session = db.execute('''SELECT 1 FROM private_local_sessions s JOIN private_final_nonces n
        ON n.session_id=s.session_id WHERE s.owner_sid=? AND s.instance_id=?
        AND n.review_digest=? AND n.decision_id=?''',
        (actor['owner_sid'],actor['instance_id'],frozen.digest(),decision['decision_id'])).fetchone()
    candidate = db.execute('SELECT * FROM private_final_candidates WHERE review_digest=?', (frozen.digest(),)).fetchone()
    plan = db.execute('SELECT * FROM release_plans WHERE plan_id=?', (frozen.plan_id,)).fetchone()
    release = db.execute('SELECT * FROM release_approvals WHERE plan_id=?', (frozen.plan_id,)).fetchone()
    snapshot = db.execute('SELECT * FROM approved_publication_snapshots WHERE plan_id=?', (frozen.plan_id,)).fetchone()
    try:
        targets = json.loads(plan['target_labels_json']) if plan else None
        normalized_plan = _plan_from_row(plan) if plan else None
    except (TypeError, ValueError):
        raise ApprovalBlocked('NATIVE_APPROVAL_DURABLE_LINK_INVALID') from None
    if (not session or not candidate
            or candidate['frozen_json'].encode() != canonical_bytes(asdict(frozen))
            or candidate['frozen_sha256'] != digest(canonical_bytes(asdict(frozen)))
            or not plan or plan['status'] != 'APPROVED' or plan['product_id'] != frozen.offer_id
            or targets != list(frozen.targets)
            or not release or not snapshot
            or release['plan_id'] != frozen.plan_id or snapshot['plan_id'] != frozen.plan_id
            or snapshot['offer_id'] != frozen.offer_id
            or snapshot['product_revision'] != normalized_plan['payload'].get('product_revision')
            or release['approval_id'] != value['release_approval_id'] or release['status'] != 'APPROVED'
            or release['approved_by'] != actor['logical_profile'] or release['user_approved'] != 1
            or release['payload_digest'] != plan['payload_digest']
            or release['confirmation_token'] != plan['confirmation_token']):
        raise ApprovalBlocked('NATIVE_APPROVAL_DURABLE_LINK_INVALID')
    try:
        document = _validated_publication_snapshot_row(snapshot, plan=normalized_plan)
    except (ImmutableReleaseError, ValueError, TypeError, KeyError):
        raise ApprovalBlocked('NATIVE_APPROVAL_SNAPSHOT_LINK_INVALID') from None
    if document['snapshot_digest'] != value['publication_snapshot_digest']:
        raise ApprovalBlocked('NATIVE_APPROVAL_SNAPSHOT_LINK_INVALID')
    return {'release_approval_id': value['release_approval_id'],
        'publication_snapshot_digest': value['publication_snapshot_digest'],
        'evidence_kind': MODE, 'execution_authority': False}


def reject_synthetic_final_approval(db, plan_id):
    """Stored synthetic decisions cannot authorize legacy runs or target writes.

    No schema is created and no registry absence is taken as authority. All
    original inert-decision guards remain after this additional rejection.
    """
    from shared_platform.private_final_decision_store import _check_private_final_schema, _frozen
    from shared_platform.release_store import ReleaseAuthorizationError
    if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='private_domain_final_approvals'").fetchone():
        return
    try:
        _check_private_final_schema(db)
        rows = db.execute("""SELECT a.*,d.owner_sid,d.evidence_kind,c.frozen_json,c.frozen_sha256
            FROM private_domain_final_approvals a
            JOIN private_final_decisions d ON d.decision_id=a.decision_id
            JOIN private_final_candidates c ON c.review_digest=a.review_digest""").fetchall()
        for row in rows:
            raw = row['frozen_json'].encode()
            frozen = _frozen(raw)
            if frozen.plan_id != plan_id:
                continue
            if digest(raw) != row['frozen_sha256'] or frozen.digest() != row['review_digest'] or row['evidence_kind'] != MODE:
                raise ReleaseAuthorizationError('synthetic final approval source is invalid')
            raise ReleaseAuthorizationError('synthetic final decision cannot authorize legacy execution')
    except ReleaseAuthorizationError:
        raise
    except Exception as error:
        raise ReleaseAuthorizationError('synthetic final decision schema/source cannot authorize legacy execution') from error
