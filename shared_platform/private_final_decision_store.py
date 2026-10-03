"""Domain-built private final review and one durable same-DB decision.

No HTTP route, browser launcher, worker or real provider is installed here.
Caller receipts, FrozenReview objects and approved_by are never accepted.
"""
from __future__ import annotations

from dataclasses import asdict
from contextlib import contextmanager
import json
import secrets
import sqlite3
import time

from shared_platform.common_offer_authority_store import (
    MODE, TARGET, PrivateCommonAuthorityStore, _fail, _json, _text, canonical_bytes, digest,
)
from shared_platform.final_review_server_admission import FrozenReview, compare_before_execution
from shared_platform.local_operator_session import LocalOperatorSessions


_TABLES = [
    """CREATE TABLE private_local_sessions (
        session_id TEXT PRIMARY KEY, capability_digest TEXT NOT NULL, csrf_digest TEXT NOT NULL,
        owner_sid TEXT NOT NULL, instance_id TEXT NOT NULL, expires_at_epoch INTEGER NOT NULL,
        created_at_epoch INTEGER NOT NULL)""",
    """CREATE TABLE private_final_candidates (
        review_digest TEXT PRIMARY KEY, reservation_id TEXT NOT NULL,
        frozen_json TEXT NOT NULL, frozen_sha256 TEXT NOT NULL, created_at_epoch INTEGER NOT NULL,
        FOREIGN KEY(reservation_id) REFERENCES common_write_attempt_reservations(reservation_id))""",
    """CREATE TABLE private_final_decisions (
        decision_id TEXT PRIMARY KEY, review_digest TEXT NOT NULL UNIQUE,
        owner_sid TEXT NOT NULL, evidence_kind TEXT NOT NULL CHECK(evidence_kind='SYNTHETIC_TEST_ONLY'),
        created_at_epoch INTEGER NOT NULL,
        FOREIGN KEY(review_digest) REFERENCES private_final_candidates(review_digest))""",
    """CREATE TABLE private_final_nonces (
        nonce_digest TEXT PRIMARY KEY, session_id TEXT NOT NULL, review_digest TEXT NOT NULL,
        expires_at_epoch INTEGER NOT NULL, decision_id TEXT,
        FOREIGN KEY(session_id) REFERENCES private_local_sessions(session_id),
        FOREIGN KEY(review_digest) REFERENCES private_final_candidates(review_digest),
        FOREIGN KEY(decision_id) REFERENCES private_final_decisions(decision_id))""",
    """CREATE TABLE private_domain_final_approvals (
        review_digest TEXT PRIMARY KEY, decision_id TEXT NOT NULL UNIQUE,
        domain_binding_json TEXT NOT NULL, domain_binding_sha256 TEXT NOT NULL,
        created_at_epoch INTEGER NOT NULL,
        FOREIGN KEY(review_digest) REFERENCES private_final_candidates(review_digest),
        FOREIGN KEY(decision_id) REFERENCES private_final_decisions(decision_id))""",
    """CREATE TABLE private_final_successors (
        review_digest TEXT PRIMARY KEY, decision_id TEXT NOT NULL,
        payload_bytes BLOB NOT NULL, payload_digest TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('UNKNOWN','COMPLETE')),
        readback_json TEXT, readback_digest TEXT,
        created_at_epoch INTEGER NOT NULL,
        FOREIGN KEY(review_digest) REFERENCES private_domain_final_approvals(review_digest))""",
]

_TRIGGERS = [
    f"CREATE TRIGGER {table}_no_{operation} BEFORE {operation.upper()} ON {table} BEGIN SELECT RAISE(ABORT,'immutable private final authority'); END"
    for table in ("private_final_candidates", "private_final_decisions", "private_domain_final_approvals", "private_local_sessions")
    for operation in ("update", "delete")
]


def _check_private_final_schema(db):
    """Require the exact six table/eight immutable-trigger definitions."""
    def normalized(sql):
        return sql.strip().rstrip(";")
    for statement in (*_TABLES, *_TRIGGERS):
        name = statement.split()[2]
        kind = statement.split()[1].lower()
        actual = db.execute("SELECT sql FROM sqlite_master WHERE name=? AND type=?", (name, kind)).fetchone()
        if not actual or not actual["sql"] or normalized(actual["sql"]) != normalized(statement):
            _fail("PRIVATE_FINAL_SCHEMA_DRIFT")


def _frozen(raw):
    value = _json(raw)
    value["targets"] = tuple(value["targets"])
    return FrozenReview(**value)


class PrivateFinalDecisionStore:
    def __init__(self, authority):
        if type(authority) is not PrivateCommonAuthorityStore:
            _fail("PRIVATE_COMMON_STORE_REQUIRED")
        self.authority = authority
        self.sessions = LocalOperatorSessions(authority)

    @contextmanager
    def _transaction(self, *, readonly=False):
        with self.authority._transaction(readonly=readonly) as db:
            _check_private_final_schema(db)
            yield db
            _check_private_final_schema(db)

    def migrate(self):
        with self.authority._transaction() as db:
            existing = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE 'private_final_%' OR name LIKE 'private_domain_%' OR name='private_local_sessions')")}
            names = {sql.split()[2] for sql in _TABLES}
            if existing:
                if existing != names:
                    _fail("PRIVATE_FINAL_SCHEMA_COLLISION")
                _check_private_final_schema(db)
                return {"status": "ALREADY_MIGRATED_PRIVATE", "execution_authority": False}
            for sql in _TABLES:
                db.execute(sql)
            for sql in _TRIGGERS:
                db.execute(sql)
            _check_private_final_schema(db)
            return {"status": "MIGRATED_PRIVATE", "execution_authority": False}

    def _build(self, db, reservation_id):
        """Rebuild only from stored actual domain bytes and exact COMMON proof."""
        reservation = db.execute("SELECT * FROM common_write_attempt_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
        if not reservation or reservation["state"] != "VERIFIED":
            _fail("VERIFIED_COMMON_REQUIRED")
        binding = _json(reservation["binding_json"].encode())
        if digest(canonical_bytes(binding)) != reservation["binding_digest"]:
            _fail("COMMON_BINDING_BYTES_INVALID")
        plan = db.execute("SELECT payload_json FROM release_plans WHERE plan_id=?", (binding["plan_id"],)).fetchone()
        if not plan:
            _fail("COMMON_PLAN_MISSING")
        payload = _json(plan["payload_json"].encode())
        mutation = canonical_bytes(payload["common_private_contract"]["mutation"])
        request = {key: binding[key] for key in ("plan_id", "run_id", "attempt", "product_revision", "payload_digest", "coverage_generation", "policy_generation")}
        current, _, _, _, authority_digest, rebuilt, attempt = self.authority._binding(db, request, mutation)
        if (canonical_bytes(rebuilt) != canonical_bytes(binding)
                or authority_digest != reservation["authority_digest"]
                or current["target_status"] != "SUCCEEDED" or current["attempts"] != attempt):
            _fail("VERIFIED_COMMON_BINDING_CHANGED")
        readback = db.execute("SELECT * FROM release_target_readbacks WHERE run_id=? AND target_label=?", (binding["run_id"], TARGET)).fetchone()
        event = db.execute("SELECT * FROM common_write_attempt_events WHERE reservation_id=? AND state='VERIFIED'", (reservation_id,)).fetchone()
        target = db.execute("SELECT external_id FROM release_target_runs WHERE run_id=? AND target_label=?", (binding["run_id"], TARGET)).fetchone()
        expected = {"schema_version": "common-private-readback/v1", "evidence_kind": MODE,
                    "identity": binding["identity"], "mutation": _json(mutation),
                    "external_id": binding["identity"]["common_item_id"],
                    "reservation_id": reservation_id, "outcome": "VERIFIED"}
        actual = canonical_bytes(expected)
        if (not readback or not event or not target or target["external_id"] != expected["external_id"]
                or any(row["evidence_json"].encode() != actual or row["evidence_digest"] != digest(actual) for row in (readback, event))):
            _fail("VERIFIED_COMMON_READBACK_INVALID")
        # These are explicit synthetic source projections, not R1/R2 production provenance.
        critical = {"identity": binding["identity"], "mutation": _json(mutation),
                    "targets": binding["targets"], "revision": binding["product_revision"],
                    "images": payload.get("private_images", []), "prices": payload.get("private_prices", {})}
        review = FrozenReview(
            offer_id=binding["identity"]["offer_id"], plan_id=binding["plan_id"],
            candidate_digest="sha256:" + digest(canonical_bytes(payload)),
            critical_content_digest="sha256:" + digest(canonical_bytes(critical)),
            targets=tuple(binding["targets"]), common_plan_id=binding["plan_id"],
            common_run_id=binding["run_id"], common_readback_digest="sha256:" + digest(actual),
            common_budget_digest="sha256:" + authority_digest,
            round1_digest="sha256:" + digest(mutation),
            round2_digest="sha256:" + digest(canonical_bytes(critical["images"])),
            action_id="private-final:" + reservation_id)
        return review, payload

    def prepare(self, grant, *, reservation_id, marketplace_plan_id=None):
        """Private service producer, not a caller-built review import."""
        _text(reservation_id, "reservation_id")
        with self._transaction() as db:
            self.sessions.authenticate(db, grant)
            if marketplace_plan_id is None:
                review, _ = self._build(db, reservation_id)
            else:
                from shared_platform.private_domain_final_review import build_registered_domain_review
                _text(marketplace_plan_id, 'marketplace_plan_id')
                review, _ = build_registered_domain_review(self, db, common_reservation_id=reservation_id, marketplace_plan_id=marketplace_plan_id)
            encoded = canonical_bytes(asdict(review))
            key = review.digest()
            existing = db.execute("SELECT * FROM private_final_candidates WHERE review_digest=?", (key,)).fetchone()
            if existing and (existing["frozen_json"].encode() != encoded or existing["frozen_sha256"] != digest(encoded)):
                _fail("PRIVATE_CANDIDATE_BYTES_INVALID")
            now = int(time.time())
            db.execute("INSERT OR IGNORE INTO private_final_candidates VALUES (?,?,?,?,?)", (key, reservation_id, encoded.decode(), digest(encoded), now))
            nonce = secrets.token_hex(32)
            db.execute("INSERT INTO private_final_nonces VALUES (?,?,?,?,NULL)", (digest(nonce.encode()), grant.session_id, key, now + 300))
            return {"review": asdict(review), "review_digest": key, "nonce": nonce,
                    "execution_authority": False, "private_contract_only": True}

    def _candidate(self, db, key):
        candidate = db.execute("SELECT * FROM private_final_candidates WHERE review_digest=?", (key,)).fetchone()
        if not candidate or digest(candidate["frozen_json"].encode()) != candidate["frozen_sha256"]:
            _fail("PRIVATE_CANDIDATE_BYTES_INVALID")
        approved = _frozen(candidate["frozen_json"].encode())
        if approved.digest() != key:
            _fail("PRIVATE_CANDIDATE_BINDING_INVALID")
        from shared_platform.private_domain_final_review import has_domain_binding, build_registered_domain_review
        if has_domain_binding(db, approved.plan_id):
            current, payload = build_registered_domain_review(self, db, common_reservation_id=candidate['reservation_id'], marketplace_plan_id=approved.plan_id)
        else:
            current, payload = self._build(db, candidate["reservation_id"])
        if compare_before_execution(approved, current).disposition != "MATCH_ONLY":
            _fail("FINAL_CANDIDATE_CHANGED")
        return candidate, approved, payload

    def decide(self, grant, *, nonce, review_digest):
        """Nonce consume, one decision and domain approval commit together."""
        _text(nonce, "nonce")
        _text(review_digest, "review_digest")
        with self._transaction() as db:
            return self._decide_in_transaction(db, grant, nonce=nonce, review_digest=review_digest)

    def _decide_in_transaction(self, db, grant, *, nonce, review_digest, native_consumer=None):
        if not db.in_transaction:
            _fail("NATIVE_DECISION_TRANSACTION_REQUIRED")
        sid = self.sessions.authenticate(db, grant)
        n = db.execute("SELECT * FROM private_final_nonces WHERE nonce_digest=?", (digest(nonce.encode()),)).fetchone()
        if not n or n["session_id"] != grant.session_id or n["review_digest"] != review_digest:
            _fail("FINAL_NONCE_BINDING_INVALID")
        _, review, _ = self._candidate(db, review_digest)
        if n["decision_id"]:
            result = self._approval(db, review_digest)
            if native_consumer is not None and not result.get("release_approval_id"):
                _fail("NATIVE_DECISION_CONTRACT_DIFFERENT")
            return result
        if n["expires_at_epoch"] <= int(time.time()):
            _fail("FINAL_NONCE_EXPIRED")
        existing = db.execute("SELECT decision_id FROM private_final_decisions WHERE review_digest=?", (review_digest,)).fetchone()
        decision = existing["decision_id"] if existing else "private-decision:" + secrets.token_hex(16)
        now = int(time.time())
        if not existing:
            encoded = canonical_bytes(asdict(review))
            db.execute("INSERT INTO private_final_decisions VALUES (?,?,?,?,?)", (decision, review_digest, sid, MODE, now))
            if native_consumer is not None:
                from shared_platform.r3_native_final_approval import NativeSoleDecisionConsumer
                if type(native_consumer) is not NativeSoleDecisionConsumer or native_consumer.decisions is not self:
                    _fail("NATIVE_DECISION_CONSUMER_REQUIRED")
                encoded = native_consumer._write_binding_in_transaction(db, review, sid, decision)
            db.execute("INSERT INTO private_domain_final_approvals VALUES (?,?,?,?,?)", (review_digest, decision, encoded.decode(), digest(encoded), now))
        if existing and native_consumer is not None:
            current = self._approval(db, review_digest)
            if not current.get("release_approval_id"):
                _fail("NATIVE_DECISION_CONTRACT_DIFFERENT")
        changed = db.execute("UPDATE private_final_nonces SET decision_id=? WHERE nonce_digest=? AND decision_id IS NULL", (decision, digest(nonce.encode())))
        if changed.rowcount != 1:
            _fail("FINAL_NONCE_CAS_LOST")
        return self._approval(db, review_digest)

    def _approval(self, db, key):
        _, frozen, _ = self._candidate(db, key)
        approval = db.execute("SELECT * FROM private_domain_final_approvals WHERE review_digest=?", (key,)).fetchone()
        decision = db.execute("SELECT * FROM private_final_decisions WHERE review_digest=?", (key,)).fetchone()
        expected = canonical_bytes(asdict(frozen))
        native = None
        binding = None
        if approval:
            try:
                binding = json.loads(approval["domain_binding_json"])
            except (TypeError, ValueError):
                _fail("PRIVATE_DOMAIN_APPROVAL_INVALID")
            if type(binding) is not dict:
                _fail("PRIVATE_DOMAIN_APPROVAL_INVALID")
        if binding and binding.get("schema_version") == "native-sole-decision-binding/v1":
            from shared_platform.r3_native_final_approval import read_native_approval_binding
            native = read_native_approval_binding(db, approval, decision, frozen)
            expected = approval["domain_binding_json"].encode()
        if (not approval or not decision or approval["decision_id"] != decision["decision_id"]
                or decision["evidence_kind"] != MODE
                or decision["owner_sid"] != self.sessions._owner()
                or approval["domain_binding_json"].encode() != expected
                or approval["domain_binding_sha256"] != digest(expected)):
            _fail("PRIVATE_DOMAIN_APPROVAL_INVALID")
        return {"decision_id": decision["decision_id"], "review_digest": key,
                "operator_subject_source": "WINDOWS_EFFECTIVE_TOKEN_USER",
                "private_stage": "APPROVED", "execution_authority": False,
                "external_writes_performed": [], **(native or {})}

    def approval(self, grant, *, review_digest):
        with self._transaction(readonly=True) as db:
            self.sessions.authenticate(db, grant)
            return self._approval(db, review_digest)


class PrivateFinalFakeTransport:
    """Closed fake transport; external writes capability is absent."""
    def __init__(self):
        self.writes = []
        self.documents = {}

    def write(self, key, actual_bytes):
        self.writes.append(actual_bytes)
        self.documents[key] = actual_bytes

    def read(self, key):
        return self.documents.get(key)


def execute_private_final_successor(store, grant, *, review_digest, transport):
    if type(store) is not PrivateFinalDecisionStore or type(transport) is not PrivateFinalFakeTransport:
        _fail("PRIVATE_FINAL_FAKE_REQUIRED")
    # Domain approval lives in this ReleaseStore; no caller receipt is consumed.
    with store._transaction() as db:
        store.sessions.authenticate(db, grant)
        approval = store._approval(db, review_digest)
        _, _, payload = store._candidate(db, review_digest)
        raw = canonical_bytes(payload)
        prior = db.execute("SELECT * FROM private_final_successors WHERE review_digest=?", (review_digest,)).fetchone()
        if prior:
            if prior["decision_id"] != approval["decision_id"] or bytes(prior["payload_bytes"]) != raw or prior["payload_digest"] != digest(raw):
                _fail("PRIVATE_SUCCESSOR_BINDING_INVALID")
            if prior["state"] == "COMPLETE":
                expected = canonical_bytes({"schema_version": "private-final-fake-readback/v1", "review_digest": review_digest, "payload_sha256": digest(raw)})
                if prior["readback_json"].encode() != expected or prior["readback_digest"] != digest(expected):
                    _fail("PRIVATE_SUCCESSOR_READBACK_INVALID")
                return {**approval, "private_stage": "COMPLETE", "fake_write_performed": False}
            claimed = False
        else:
            db.execute("INSERT INTO private_final_successors VALUES (?,?,?,?,'UNKNOWN',NULL,NULL,?)", (review_digest, approval["decision_id"], raw, digest(raw), int(time.time())))
            claimed = True
    if claimed:
        # Recheck actual domain evidence at the last fake-writer boundary.
        # Drift leaves the existing UNKNOWN row; it must never create a retry.
        with store._transaction(readonly=True) as db:
            store.sessions.authenticate(db, grant)
            store._approval(db, review_digest)
            _, _, current_payload = store._candidate(db, review_digest)
            if canonical_bytes(current_payload) != raw:
                _fail("PRIVATE_SUCCESSOR_PAYLOAD_CHANGED")
        transport.write(review_digest, raw)
    elif transport.read(review_digest) is None:
        return {**approval, "private_stage": "RECONCILIATION_REQUIRED", "fake_write_performed": False}
    observed = transport.read(review_digest)
    if observed != raw:
        _fail("PRIVATE_SUCCESSOR_READBACK_MISMATCH")
    evidence = canonical_bytes({"schema_version": "private-final-fake-readback/v1", "review_digest": review_digest, "payload_sha256": digest(observed)})
    with store._transaction() as db:
        store.sessions.authenticate(db, grant)
        store._approval(db, review_digest)
        db.execute("UPDATE private_final_successors SET state='COMPLETE',readback_json=?,readback_digest=? WHERE review_digest=? AND state='UNKNOWN'", (evidence.decode(), digest(evidence), review_digest))
    return {**approval, "private_stage": "COMPLETE", "fake_write_performed": claimed}
