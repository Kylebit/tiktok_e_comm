"""Actual producer byte interface + closed authority adapter; no fake official proof."""
import json
import sqlite3

import pytest

from test_private_local_final_review import _ready
from shared_platform.private_domain_final_review import initialize_domain_bindings, register_domain_candidate, _checked_candidate
from shared_platform.r3_frozen_review_producer import DomainFrozenCandidate, DomainReviewBlocked
from shared_platform.r3_frozen_review_producer import read_stored_domain_graph
from shared_platform.r3_domain_common_proof import PrivateDomainCommonProofReader
from shared_platform.private_final_decision_store import PrivateFinalDecisionStore


def _complete_graph_without_common_proof(tmp_path, monkeypatch):
    from shared_platform import release_store
    from test_b4b_publication_preview import reviewed_marketplace
    from test_common_offer_authority_store import _authority

    # Install the real owner-bound private schema, but import no COMMON proof.
    # Only the fixture's store factory and lowest closed network I/O differ;
    # compiler, stored graph, typed reader and registration remain actual.
    tmp_path.mkdir()
    authority = _authority(tmp_path, imported=False)
    with monkeypatch.context() as patch:
        patch.setattr(release_store, 'ReleaseStore', lambda *_args, **_kwargs: authority.store)
        documents, _dashboard, domain, data, io, market = reviewed_marketplace(tmp_path, monkeypatch)
    assert domain is authority.store and io.mutations == 1
    domain.create_plan(market['plan']['payload'])
    store = PrivateFinalDecisionStore(authority)
    store.migrate()
    store.sessions.initialize_owner()
    reader = PrivateDomainCommonProofReader(authority)
    reader.initialize_fixture_schema()
    initialize_domain_bindings(store)
    with store._transaction(readonly=True) as db:
        reader.validate_context(db)
        graph = read_stored_domain_graph(db, data['plan_id'])
        assert graph.marketplace_plan_id != graph.common_plan_id
        assert graph.targets == tuple(documents['round1_snapshot']['canonical_targets']) == ('tiktok:LH_PH', 'ozon:RU')
        assert len(json.loads(graph.manifest_bytes)['variants']) == 2
        assert graph.round1_digest == documents['round1_snapshot']['snapshot_digest']
        assert db.execute('SELECT COUNT(*) FROM private_domain_common_proofs').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM common_write_attempt_reservations').fetchone()[0] == 0
    return store, data['plan_id']


def test_actual_domain_bytes_interface_and_registration_stays_blocked(tmp_path, monkeypatch):
    store, _, _ = _ready(tmp_path)
    initialize_domain_bindings(store)
    # This is only a container interface check, never a domain authority input.
    # It catches manifest(method) / manifest_bytes(bytes) confusion directly.
    with store._transaction(readonly=True) as db:
        review, _ = store._build(db, 'private-reservation')
    container = DomainFrozenCandidate(review, b'{"display":"private"}', b'{"manifest":"private"}', 'private-reservation')
    assert _checked_candidate(container, review.plan_id).manifest_bytes == b'{"manifest":"private"}'
    assert callable(container.manifest)
    # The typed reader now validates its schema and complete stored graph
    # before checking proof availability. Reach that same original authority
    # refusal using those real prerequisites, without importing any proof.
    store, marketplace_plan_id = _complete_graph_without_common_proof(tmp_path / 'complete-domain', monkeypatch)
    with pytest.raises(DomainReviewBlocked, match='^COMMON_TRUSTED_BUDGET_AND_OFFICIAL_READBACK_REQUIRED$'):
        register_domain_candidate(store, common_reservation_id='unadmitted-common-proof', marketplace_plan_id=marketplace_plan_id)
    with sqlite3.connect(store.authority.store.path) as db:
        for table in ('private_domain_review_bindings', 'private_final_candidates', 'private_final_decisions', 'private_domain_final_approvals'):
            assert db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM private_domain_common_proofs').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM common_write_attempt_reservations').fetchone()[0] == 0


def test_domain_registration_missing_typed_schema_rejected_before_authority_lookup(tmp_path):
    store, _, _ = _ready(tmp_path)
    initialize_domain_bindings(store)
    with pytest.raises(DomainReviewBlocked, match='^DOMAIN_FIXTURE_SCHEMA_REQUIRED$'):
        register_domain_candidate(store, common_reservation_id='private-reservation', marketplace_plan_id='retained-marketplace')
    with sqlite3.connect(store.authority.store.path) as db:
        for table in ('private_domain_review_bindings', 'private_final_candidates', 'private_final_decisions', 'private_domain_final_approvals'):
            assert db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='private_domain_common_proofs'").fetchone()[0] == 0
