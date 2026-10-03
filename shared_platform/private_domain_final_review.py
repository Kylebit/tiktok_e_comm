"""Private service registration of domain-produced R3 bytes in release.db.

This adapter never imports a caller FrozenReview, display manifest, targets or
approval. Production authority is absent; the exact domain producer must rebuild
the marketplace graph with private COMMON proof on each prepare/decision.
"""
from __future__ import annotations

from dataclasses import asdict

from shared_platform.common_offer_authority_store import canonical_bytes, digest, _fail, _json, _text
from shared_platform.final_review_server_admission import FrozenReview


_SCHEMA = """CREATE TABLE private_domain_review_bindings (
    marketplace_plan_id TEXT PRIMARY KEY, common_reservation_id TEXT NOT NULL,
    review_json TEXT NOT NULL, review_sha256 TEXT NOT NULL,
    display_bytes BLOB NOT NULL, display_sha256 TEXT NOT NULL,
    manifest_bytes BLOB NOT NULL, manifest_sha256 TEXT NOT NULL,
    FOREIGN KEY(common_reservation_id) REFERENCES common_write_attempt_reservations(reservation_id))"""
_TRIGGERS = [f"CREATE TRIGGER private_domain_review_bindings_no_{operation} BEFORE {operation.upper()} ON private_domain_review_bindings BEGIN SELECT RAISE(ABORT,'immutable domain review binding'); END" for operation in ('update', 'delete')]


def _schema(db):
    for statement in (_SCHEMA, *_TRIGGERS):
        kind, name = statement.split()[1:3]
        row = db.execute('SELECT sql FROM sqlite_master WHERE type=? AND name=?', (kind.lower(), name)).fetchone()
        if not row or row['sql'].strip().rstrip(';') != statement.strip().rstrip(';'):
            _fail('PRIVATE_DOMAIN_REVIEW_SCHEMA_DRIFT')


def initialize_domain_bindings(store):
    from shared_platform.private_final_decision_store import PrivateFinalDecisionStore
    if type(store) is not PrivateFinalDecisionStore:
        _fail('PRIVATE_DOMAIN_FINAL_STORE_REQUIRED')
    store.sessions._owner()
    with store._transaction() as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='private_domain_review_bindings'").fetchone():
            db.execute(_SCHEMA)
            for statement in _TRIGGERS:
                db.execute(statement)
        _schema(db)


def _checked_candidate(candidate, marketplace_plan_id):
    """Check the actual producer's byte interface, without granting authority."""
    from shared_platform.r3_frozen_review_producer import DomainFrozenCandidate
    if (type(candidate) is not DomainFrozenCandidate or type(candidate.review) is not FrozenReview or type(candidate.display_bytes) is not bytes
            or type(candidate.manifest_bytes) is not bytes or candidate.review.plan_id != marketplace_plan_id):
        _fail('DOMAIN_FROZEN_PRODUCER_INVALID')
    return candidate


def _produce(store, db, reservation_id, marketplace_plan_id):
    from shared_platform.r3_frozen_review_producer import DomainFrozenReviewProducer
    candidate = DomainFrozenReviewProducer(store.authority).build(db, reservation_id, marketplace_plan_id)
    return _checked_candidate(candidate, marketplace_plan_id)


def register_domain_candidate(store, *, common_reservation_id, marketplace_plan_id):
    """Owner process preparation; no ordinary HTTP registration endpoint."""
    _text(common_reservation_id, 'common_reservation_id')
    _text(marketplace_plan_id, 'marketplace_plan_id')
    store.sessions._owner()
    with store._transaction() as db:
        _schema(db)
        candidate = _produce(store, db, common_reservation_id, marketplace_plan_id)
        review = canonical_bytes(asdict(candidate.review))
        values = (marketplace_plan_id, common_reservation_id, review.decode(), digest(review),
                  candidate.display_bytes, digest(candidate.display_bytes), candidate.manifest_bytes, digest(candidate.manifest_bytes))
        previous = db.execute('SELECT * FROM private_domain_review_bindings WHERE marketplace_plan_id=?', (marketplace_plan_id,)).fetchone()
        if previous:
            existing = (previous['marketplace_plan_id'], previous['common_reservation_id'], previous['review_json'], previous['review_sha256'],
                        bytes(previous['display_bytes']), previous['display_sha256'], bytes(previous['manifest_bytes']), previous['manifest_sha256'])
            if existing != values:
                _fail('DOMAIN_REGISTERED_CONTENT_CHANGED')
        else:
            db.execute('INSERT INTO private_domain_review_bindings VALUES (?,?,?,?,?,?,?,?)', values)
        return candidate.descriptor()


def has_domain_binding(db, marketplace_plan_id):
    if not db.execute("SELECT 1 FROM sqlite_master WHERE name='private_domain_review_bindings'").fetchone():
        return False
    _schema(db)
    return db.execute('SELECT 1 FROM private_domain_review_bindings WHERE marketplace_plan_id=?', (marketplace_plan_id,)).fetchone() is not None


def build_registered_domain_review(store, db, *, common_reservation_id, marketplace_plan_id):
    _schema(db)
    row = db.execute('SELECT * FROM private_domain_review_bindings WHERE marketplace_plan_id=?', (marketplace_plan_id,)).fetchone()
    if not row or row['common_reservation_id'] != common_reservation_id:
        _fail('DOMAIN_REVIEW_BINDING_UNAVAILABLE')
    if (digest(row['review_json'].encode()) != row['review_sha256']
            or digest(bytes(row['display_bytes'])) != row['display_sha256']
            or digest(bytes(row['manifest_bytes'])) != row['manifest_sha256']):
        _fail('DOMAIN_REGISTERED_BYTES_INVALID')
    candidate = _produce(store, db, common_reservation_id, marketplace_plan_id)
    if (canonical_bytes(asdict(candidate.review)) != row['review_json'].encode()
            or candidate.display_bytes != bytes(row['display_bytes']) or candidate.manifest_bytes != bytes(row['manifest_bytes'])):
        _fail('DOMAIN_REGISTERED_CONTENT_CHANGED')
    # Fake successor bytes include the exact graph and full displayed material.
    payload = {'schema_version': 'domain-final-review-private-successor/v1',
               'evidence_kind': 'SYNTHETIC_TEST_ONLY', 'execution_authority': False,
               'review': asdict(candidate.review), 'display': _json(candidate.display_bytes),
               'manifest': _json(candidate.manifest_bytes)}
    return candidate.review, payload


def project_registered_domain_stage(store, view):
    """Read-only attach to the original full preview; never rewrite its content."""
    from copy import deepcopy
    result = deepcopy(view)
    market = result.get('marketplace') or {}
    plan = market.get('plan') or {}
    preview = market.get('preview') or market.get('candidate') or {}
    if not plan.get('plan_id'):
        return result
    with store._transaction(readonly=True) as db:
        if not has_domain_binding(db, plan['plan_id']):
            return result
        row = db.execute('SELECT common_reservation_id FROM private_domain_review_bindings WHERE marketplace_plan_id=?', (plan['plan_id'],)).fetchone()
        review, _ = build_registered_domain_review(store, db, common_reservation_id=row['common_reservation_id'], marketplace_plan_id=plan['plan_id'])
        if (review.offer_id != result.get('offer_id')
                or review.candidate_digest != preview.get('candidate_digest')
                or tuple(preview.get('target_labels') or ()) != review.targets):
            _fail('DOMAIN_DISPLAYED_CANDIDATE_CHANGED')
        candidate = _produce(store, db, row['common_reservation_id'], plan['plan_id'])
        if canonical_bytes(preview) != candidate.display_bytes:
            _fail('DOMAIN_DISPLAYED_BYTES_CHANGED')
        descriptor = candidate.descriptor()
        if descriptor.get('evidence_kind') != 'SYNTHETIC_TEST_ONLY' or descriptor.get('execution_authority') is not False:
            _fail('PRIVATE_DOMAIN_REVIEW_SCOPE_REQUIRED')
        native_plan = db.execute('SELECT status FROM release_plans WHERE plan_id=?', (plan['plan_id'],)).fetchone()
        if not native_plan or native_plan['status'] != 'PENDING_APPROVAL':
            _fail('PRIVATE_DOMAIN_PLAN_STATE_REQUIRED')
        descriptor['private_domain_plan_status'] = native_plan['status']
        decision = db.execute('SELECT decision_id FROM private_final_decisions WHERE review_digest=?', (review.digest(),)).fetchone()
        descriptor['approval_saved'] = bool(decision)
        if decision:
            descriptor['decision_id'] = store._approval(db, review.digest())['decision_id']
            successor = db.execute('SELECT state FROM private_final_successors WHERE review_digest=?', (review.digest(),)).fetchone()
            descriptor['private_successor_state'] = successor['state'] if successor else 'NOT_STARTED'
        market['local_operator_review'] = descriptor
        # This is a separate private presentation capability. Do not overwrite
        # real final_review_admission or grant legacy/provider execution.
        market['real_publication_status'] = market.get('status')
        market['private_final_review_available'] = True
        market['status'] = 'APPROVAL_REQUIRED'
    return result
