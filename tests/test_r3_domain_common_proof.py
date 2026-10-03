"""Complete domain graph and OS-owned synthetic authority, never provider proof.

Only the existing fixture's lowest network I/O is fake. The actual compiler,
ReleaseStore, producer, typed reader and same SQLite transaction are exercised.
"""
import json
import hashlib
import sqlite3

import pytest

from shared_platform.common_offer_authority_store import (
    MODE, CommonAuthorityBlocked, PrivateCommonAuthorityStore,
)
from shared_platform.local_operator_session import LocalOperatorSessions
from shared_platform.private_final_decision_store import PrivateFinalDecisionStore
from shared_platform.r3_domain_common_proof import PrivateDomainCommonProofReader
from shared_platform.r3_frozen_review_producer import DomainFrozenReviewProducer, DomainReviewBlocked


@pytest.fixture
def domain_private(tmp_path, monkeypatch):
    from shared_platform import release_store
    from test_b4b_publication_preview import reviewed_marketplace
    authority = PrivateCommonAuthorityStore.create(tmp_path / "owned")
    authority.migrate()
    PrivateFinalDecisionStore(authority).migrate()
    LocalOperatorSessions(authority).initialize_owner()
    # Route the existing actual stored-domain fixture into the owner DB. This
    # is a store factory boundary, not a mock of graph/build/proof/approval.
    with monkeypatch.context() as patch:
        patch.setattr(release_store, "ReleaseStore", lambda _path: authority.store)
        documents, dashboard, store, data, io, market = reviewed_marketplace(tmp_path, patch)
    assert store.path == authority.store.path and io.mutations == 0
    assert market["final_review_available"] is False
    assert market["final_review_admission"]["execution_authority"] is False
    authority.store.create_plan(market["plan"]["payload"])
    reader = PrivateDomainCommonProofReader(authority)
    reader.initialize_fixture_schema()
    receipt = reader.register_fixture(market["plan"]["plan_id"])
    return authority, reader, receipt, market["plan"], documents


def build_fixture(value):
    authority, _reader, receipt, market, _documents = value
    with authority._transaction(readonly=True) as db:
        return DomainFrozenReviewProducer(authority).build(db, receipt["reservation_id"], market["plan_id"])


def test_actual_full_graph_build_has_distinct_plan_full_matrix_and_explicit_synthetic_authority(domain_private):
    authority, reader, receipt, market, docs = domain_private
    candidate = build_fixture(domain_private)
    review = candidate.review
    display = json.loads(candidate.display_bytes)
    manifest = json.loads(candidate.manifest_bytes)
    assert review.plan_id == market["plan_id"] != review.common_plan_id
    assert review.targets == ("tiktok:LH_PH", "ozon:RU")
    assert review.round1_digest == docs["round1_snapshot"]["snapshot_digest"]
    assert len(manifest["variants"]) == 2 and manifest["copy_sets"] and manifest["image_sets"]
    assert display["review_manifest"] == manifest
    assert review.common_budget_digest.startswith("sha256:")
    assert review.common_readback_digest.startswith("sha256:")
    descriptor = candidate.descriptor()
    assert descriptor["evidence_kind"] == MODE and descriptor["execution_authority"] is False
    assert receipt["evidence_kind"] == MODE and receipt["external_writes_performed"] == []


def test_build_is_read_only_replays_identically_and_register_is_idempotent(domain_private):
    authority, reader, receipt, market, _ = domain_private
    def rows():
        with authority._transaction(readonly=True) as db:
            return {r[0]: [tuple(v) for v in db.execute('SELECT * FROM "' + r[0] + '"')]
                    for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    before = rows()
    first = build_fixture(domain_private)
    assert build_fixture(domain_private) == first
    assert reader.register_fixture(market["plan_id"])["reservation_id"] == receipt["reservation_id"]
    assert rows() == before


def test_missing_real_authority_never_uses_private_fixture_as_default(domain_private):
    authority, _reader, receipt, market, _ = domain_private
    with authority._transaction(readonly=True) as db:
        with pytest.raises(DomainReviewBlocked, match="COMMON_TRUSTED_BUDGET_AND_OFFICIAL_READBACK_REQUIRED"):
            DomainFrozenReviewProducer().build(db, receipt["reservation_id"], market["plan_id"])


def test_proof_requires_the_same_database_and_explicit_snapshot(domain_private, tmp_path):
    authority, reader, receipt, market, _ = domain_private
    with sqlite3.connect(tmp_path / "alien.db") as db:
        db.row_factory = sqlite3.Row
        db.execute("BEGIN")
        with pytest.raises(DomainReviewBlocked, match="DOMAIN_PROOF_DATABASE_MISMATCH"):
            DomainFrozenReviewProducer(authority).build(db, receipt["reservation_id"], market["plan_id"])
    with authority.store._connect_readonly() as db:
        with pytest.raises(DomainReviewBlocked, match="DOMAIN_SQLITE_SNAPSHOT_REQUIRED"):
            DomainFrozenReviewProducer(authority).build(db, receipt["reservation_id"], market["plan_id"])


@pytest.mark.parametrize("drift", ["target_state", "attempt_count", "extra_common_run", "proof_schema", "proof_trigger", "readback", "manifest"])
def test_source_and_authority_drift_block_without_new_decision(domain_private, drift):
    authority, reader, receipt, market, _ = domain_private
    binding = market["payload"]["r3_marketplace_binding"]
    with authority._transaction() as db:
        if drift == "target_state":
            db.execute("UPDATE release_target_runs SET status='RUNNING' WHERE run_id=?", (binding["common_run_id"],))
        elif drift == "attempt_count":
            db.execute("UPDATE release_target_runs SET attempts=2 WHERE run_id=?", (binding["common_run_id"],))
        elif drift == "extra_common_run":
            for table, idfield, oldid, newid in (("release_plans", "plan_id", binding["common_plan_id"], "extra-fixture-plan"),
                                                ("release_runs", "run_id", binding["common_run_id"], "extra-fixture-run")):
                row = dict(db.execute(f"SELECT * FROM {table} WHERE {idfield}=?", (oldid,)).fetchone())
                row[idfield] = newid
                if table == "release_plans":
                    extra_payload = json.loads(row["payload_json"])
                    extra_payload["plan_id"] = newid
                    row["payload_json"] = json.dumps(extra_payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                    row["payload_digest"] = hashlib.sha256(row["payload_json"].encode()).hexdigest()
                    row["confirmation_token"] = "extra-fixture-token"
                if table == "release_runs":
                    row["plan_id"] = "extra-fixture-plan"
                columns = ','.join(row)
                db.execute(f"INSERT INTO {table} ({columns}) VALUES ({','.join('?' for _ in row)})", tuple(row.values()))
        elif drift == "proof_schema":
            db.execute("ALTER TABLE private_domain_common_proofs ADD COLUMN attacker TEXT")
        elif drift == "proof_trigger":
            db.execute("DROP TRIGGER private_domain_common_proofs_no_update")
        elif drift == "readback":
            db.execute("UPDATE release_target_readbacks SET evidence_digest='changed' WHERE run_id=?", (binding["common_run_id"],))
        else:
            # Deliberately bypass the existing immutable-payload trigger in
            # this private adversarial fixture. A freshly checksummed forged
            # display still must fail actual lineage reconstruction.
            payload = json.loads(db.execute("SELECT payload_json FROM release_plans WHERE plan_id=?", (market["plan_id"],)).fetchone()[0])
            payload["r3_marketplace_binding"]["reviewed_candidate_facts"]["review_manifest"]["product"]["title"] = "forged after review"
            raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            db.execute("DROP TRIGGER trg_release_plan_immutable")
            db.execute("UPDATE release_plans SET payload_json=?,payload_digest=? WHERE plan_id=?", (raw, hashlib.sha256(raw.encode()).hexdigest(), market["plan_id"]))
    with pytest.raises((DomainReviewBlocked, CommonAuthorityBlocked)):
        build_fixture(domain_private)
    with authority._transaction(readonly=True) as db:
        assert db.execute("SELECT count(*) FROM private_final_decisions").fetchone()[0] == 0


def test_fixture_binding_cannot_be_consumed_as_a_legacy_common_dispatch(domain_private):
    authority, _reader, receipt, market, _ = domain_private
    with authority._transaction(readonly=True) as db:
        row = db.execute("SELECT * FROM common_write_attempt_reservations WHERE reservation_id=?", (receipt["reservation_id"],)).fetchone()
        binding = json.loads(row["binding_json"])
        plan = db.execute("SELECT * FROM release_plans WHERE plan_id=?", (row["plan_id"],)).fetchone()
        payload = json.loads(plan["payload_json"])
        assert binding["schema_version"] == "private-domain-common-fixture-binding/v1"
        assert binding["evidence_kind"] == MODE
    with pytest.raises(CommonAuthorityBlocked, match="IMPORT_FIELDS_INVALID"):
        authority.consume_private(receipt["reservation_id"],
                                  {"plan_id": row["plan_id"], "run_id": row["run_id"], "attempt": 1,
                                   "product_revision": payload["product_revision"], "payload_digest": plan["payload_digest"],
                                   "coverage_generation": 1, "policy_generation": 1}, b"{}")


def test_fixture_table_is_immutable_and_missing_budget_is_not_zero(domain_private):
    authority, _reader, receipt, market, _ = domain_private
    with pytest.raises(sqlite3.IntegrityError):
        with authority._transaction() as db:
            db.execute("DELETE FROM private_domain_common_proofs")
    with authority._transaction() as db:
        db.execute("DROP TABLE private_domain_common_proofs")
    with pytest.raises(DomainReviewBlocked, match="DOMAIN_FIXTURE_SCHEMA_REQUIRED"):
        build_fixture(domain_private)


def test_owner_marker_change_does_not_yield_a_review(domain_private):
    authority, _reader, _receipt, _market, _ = domain_private
    marker = authority.root / "local-operator" / "owner.json"
    original = marker.read_bytes()
    marker.write_bytes(b"{}")
    try:
        with pytest.raises(CommonAuthorityBlocked, match="LOCAL_OWNER_INSTANCE_MISMATCH"):
            build_fixture(domain_private)
    finally:
        marker.write_bytes(original)


def test_insert_or_replace_cannot_rewrite_immutable_fixture_proof(domain_private):
    authority, _reader, receipt, market, _ = domain_private
    with pytest.raises(sqlite3.IntegrityError, match="immutable synthetic domain proof"):
        with authority._transaction() as db:
            db.execute("INSERT OR REPLACE INTO private_domain_common_proofs VALUES (?,?,?,?)",
                       (receipt["reservation_id"], market["plan_id"], "{}", "forged"))
    assert build_fixture(domain_private).evidence_kind == MODE
