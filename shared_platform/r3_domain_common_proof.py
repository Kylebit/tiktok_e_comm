"""Service-owned COMMON proof boundary; the only concrete reader is synthetic.

Fixture import is native, explicit, owner-bound and confined to a newly created
private ReleaseStore. It records completed closed fixture I/O, never grants a
dispatch capability or labels its official-shaped readback as official fact.
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
import json
import sqlite3
import time

from shared_platform.common_offer_authority_store import (
    MODE, TARGET, PrivateCommonAuthorityStore, canonical_bytes, digest,
)
from shared_platform.local_operator_session import LocalOperatorSessions
from shared_platform.r3_frozen_review_producer import (
    DomainReviewBlocked, _sha, read_stored_domain_graph,
)


_TABLE = """CREATE TABLE private_domain_common_proofs (
    reservation_id TEXT PRIMARY KEY, marketplace_plan_id TEXT NOT NULL UNIQUE,
    proof_json TEXT NOT NULL, proof_digest TEXT NOT NULL,
    FOREIGN KEY(reservation_id) REFERENCES common_write_attempt_reservations(reservation_id))"""
_TRIGGERS = [f"CREATE TRIGGER private_domain_common_proofs_no_{action.lower()} BEFORE {action} "
             "ON private_domain_common_proofs BEGIN SELECT RAISE(ABORT,'immutable synthetic domain proof'); END"
             for action in ("UPDATE", "DELETE")]
_TRIGGERS.append("CREATE TRIGGER private_domain_common_proofs_no_replace BEFORE INSERT "
                 "ON private_domain_common_proofs WHEN EXISTS (SELECT 1 FROM private_domain_common_proofs "
                 "WHERE reservation_id=NEW.reservation_id OR marketplace_plan_id=NEW.marketplace_plan_id) "
                 "BEGIN SELECT RAISE(ABORT,'immutable synthetic domain proof'); END")


def _check_schema(db):
    expected = {"private_domain_common_proofs": _TABLE,
                **{f"private_domain_common_proofs_no_{a.lower()}": sql
                   for a, sql in zip(("UPDATE", "DELETE", "REPLACE"), _TRIGGERS)}}
    for name, sql in expected.items():
        row = db.execute("SELECT type,sql FROM sqlite_master WHERE name=?", (name,)).fetchone()
        if (not row or row[0] != ("table" if name == "private_domain_common_proofs" else "trigger")
                or " ".join(row[1].split()) != " ".join(sql.split())):
            raise DomainReviewBlocked("DOMAIN_FIXTURE_SCHEMA_REQUIRED")


def _graph_binding(graph):
    return {"offer_id": graph.offer_id, "marketplace_plan_id": graph.marketplace_plan_id,
            "common_plan_id": graph.common_plan_id, "common_run_id": graph.common_run_id,
            "targets": list(graph.targets), "candidate_digest": graph.candidate_digest,
            "critical_content_digest": graph.critical_content_digest,
            "round1_digest": graph.round1_digest, "round2_digest": graph.round2_digest,
            "display_digest": _sha(graph.display_bytes), "manifest_digest": _sha(graph.manifest_bytes),
            "readback_digest": graph.common_readback_digest}


@dataclass(frozen=True)
class DomainCommonProof:
    common_reservation_id: str
    marketplace_plan_id: str
    common_plan_id: str
    common_run_id: str
    graph_digest: str
    readback_digest: str
    budget_digest: str
    evidence_kind: str


class DomainCommonProofReader(ABC):
    @abstractmethod
    def read_verified(self, db, common_reservation_id, marketplace_plan_id, graph):
        """Read from the caller's same transaction; no HTTP-supplied proof."""
        raise NotImplementedError


class PrivateDomainCommonProofReader(DomainCommonProofReader):
    def __init__(self, authority):
        if type(authority) is not PrivateCommonAuthorityStore:
            raise DomainReviewBlocked("PRIVATE_DOMAIN_AUTHORITY_REQUIRED")
        self.authority = authority

    def validate_context(self, db):
        self.authority._guard()
        LocalOperatorSessions(self.authority)._owner()
        if not db.in_transaction:
            raise DomainReviewBlocked("DOMAIN_SQLITE_SNAPSHOT_REQUIRED")
        paths = [row[2] for row in db.execute("PRAGMA database_list") if row[1] == "main"]
        if len(paths) != 1 or Path(paths[0]).resolve() != self.authority.store.path.resolve():
            raise DomainReviewBlocked("DOMAIN_PROOF_DATABASE_MISMATCH")
        _check_schema(db)

    def initialize_fixture_schema(self):
        LocalOperatorSessions(self.authority)._owner()
        with self.authority._transaction() as db:
            if db.execute("SELECT 1 FROM sqlite_master WHERE name LIKE 'private_domain_common_proofs%' LIMIT 1").fetchone():
                _check_schema(db)
                return
            db.execute(_TABLE)
            for sql in _TRIGGERS:
                db.execute(sql)
            _check_schema(db)

    def _material(self, db, graph):
        # This deliberately narrow fixture importer cannot claim incomplete
        # live historical coverage. The private instance must contain exactly
        # one completed COMMON attempt for this Offer, across *all* plans/runs.
        candidates = [dict(row) for row in db.execute("""SELECT p.plan_id,r.run_id,t.target_label,
            p.target_labels_json AS retained_plan_targets_json,
            t.status,t.attempts,t.external_id FROM release_plans p
            LEFT JOIN release_runs r ON r.plan_id=p.plan_id
            LEFT JOIN release_target_runs t ON t.run_id=r.run_id AND t.target_label=?
            WHERE p.product_id=?
            ORDER BY p.plan_id,r.run_id""", (TARGET, graph.offer_id))]
        rows = []
        for candidate in candidates:
            try:
                labels = json.loads(candidate.pop("retained_plan_targets_json"))
            except (TypeError, ValueError):
                raise DomainReviewBlocked("DOMAIN_FIXTURE_HISTORY_INCOMPLETE_OR_CHANGED") from None
            if type(labels) is not list or any(type(label) is not str for label in labels):
                raise DomainReviewBlocked("DOMAIN_FIXTURE_HISTORY_INCOMPLETE_OR_CHANGED")
            # Include mixed-target COMMON plans and orphan/mismatched COMMON
            # target rows. A draft with no run is not complete history either.
            if TARGET in labels or candidate["target_label"] == TARGET:
                rows.append(candidate)
        if (len(rows) != 1 or rows[0]["plan_id"] != graph.common_plan_id
                or rows[0]["run_id"] != graph.common_run_id or rows[0]["target_label"] != TARGET
                or rows[0]["status"] != "SUCCEEDED" or rows[0]["attempts"] != 1
                or not rows[0]["external_id"]):
            raise DomainReviewBlocked("DOMAIN_FIXTURE_HISTORY_INCOMPLETE_OR_CHANGED")
        budget = {"schema_version": "private-domain-fixture-budget/v1", "evidence_kind": MODE,
                  "instance_id": self.authority.marker["instance_id"], "offer_id": graph.offer_id,
                  "baseline_count": 0, "maximum_attempts": 1, "retained_attempt_count": 1,
                  "attempts": rows}
        binding = _graph_binding(graph)
        return {"schema_version": "private-domain-common-proof/v1", "evidence_kind": MODE,
                "instance_id": self.authority.marker["instance_id"],
                "owner_sid": LocalOperatorSessions(self.authority)._owner(),
                "graph": binding, "graph_digest": _sha(canonical_bytes(binding)),
                "readback_kind": "SYNTHETIC_OFFICIAL_SHAPE_ONLY", "budget": budget,
                "budget_digest": _sha(canonical_bytes(budget))}

    def register_fixture(self, marketplace_plan_id):
        """Import completed closed fixture facts; no claim or provider dispatch.

        The distinct binding and packet schemas cannot be consumed by the old
        common-private-plan/v1 authority. This only supplies its existing FK.
        """
        with self.authority._transaction() as db:
            self.validate_context(db)
            graph = read_stored_domain_graph(db, marketplace_plan_id, category_store=self.authority.store)
            proof = self._material(db, graph)
            raw = canonical_bytes(proof)
            reservation_id = "synthetic-domain-" + digest(canonical_bytes(
                [self.authority.marker["instance_id"], graph.common_plan_id, graph.common_run_id]))
            existing = db.execute("SELECT * FROM private_domain_common_proofs WHERE marketplace_plan_id=?", (marketplace_plan_id,)).fetchone()
            if existing:
                self.read_verified(db, existing["reservation_id"], marketplace_plan_id, graph)
                return self._receipt(existing["reservation_id"])
            # No silent adoption of an earlier ledger reservation or policy.
            scope = "synthetic-domain-scope-" + digest(canonical_bytes([self.authority.marker["instance_id"], graph.offer_id]))
            if db.execute("SELECT 1 FROM common_write_attempt_reservations WHERE scope_key=? OR (run_id=? AND target_label=?)", (scope, graph.common_run_id, TARGET)).fetchone():
                raise DomainReviewBlocked("DOMAIN_FIXTURE_RESERVATION_COLLISION")
            packet = {"schema_version": "private-domain-completed-fixture-import/v1", "evidence_kind": MODE,
                      "proof_digest": _sha(raw), "execution_authority": False}
            packet_raw = canonical_bytes(packet)
            imported = digest(packet_raw)
            now = int(time.time())
            db.execute("INSERT INTO common_authority_imports VALUES (?,?,?,?)",
                       (imported, packet_raw.decode(), "native-synthetic-domain-fixture", now))
            # Explicit synthetic FK parents, distinct from old policy/coverage
            # contracts; neither can establish production COMMON authority.
            for table, material in (("common_offer_history_coverage", proof["budget"]),
                                    ("common_standing_policy_versions", packet)):
                encoded = canonical_bytes(material)
                db.execute(f"INSERT INTO {table} VALUES (?,?,?,?,?)", (scope, 1, digest(encoded), encoded.decode(), imported))
            binding = {"schema_version": "private-domain-common-fixture-binding/v1", "evidence_kind": MODE,
                       "common_plan_id": graph.common_plan_id, "common_run_id": graph.common_run_id,
                       "target_label": TARGET, "attempt": 1, "proof_digest": _sha(raw)}
            binding_raw = canonical_bytes(binding)
            db.execute("INSERT INTO common_write_attempt_reservations VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                       (reservation_id, scope, graph.common_plan_id, graph.common_run_id, TARGET, 1,
                        binding_raw.decode(), digest(binding_raw), 1, 1, imported, "VERIFIED", now, now))
            self.authority._event(db, reservation_id, "VERIFIED", packet, now)
            db.execute("INSERT INTO private_domain_common_proofs VALUES (?,?,?,?)",
                       (reservation_id, marketplace_plan_id, raw.decode(), digest(raw)))
            self.read_verified(db, reservation_id, marketplace_plan_id, graph)
            return self._receipt(reservation_id)

    @staticmethod
    def _receipt(reservation_id):
        return {"reservation_id": reservation_id, "common_reservation_id": reservation_id,
                "evidence_kind": MODE, "execution_authority": False, "external_writes_performed": []}

    def read_verified(self, db, common_reservation_id, marketplace_plan_id, graph):
        self.validate_context(db)
        row = db.execute("SELECT * FROM private_domain_common_proofs WHERE reservation_id=? AND marketplace_plan_id=?",
                         (common_reservation_id, marketplace_plan_id)).fetchone()
        if not row:
            raise DomainReviewBlocked("COMMON_TRUSTED_BUDGET_AND_OFFICIAL_READBACK_REQUIRED")
        expected = self._material(db, graph)
        raw = canonical_bytes(expected)
        reservation = db.execute("SELECT * FROM common_write_attempt_reservations WHERE reservation_id=?", (common_reservation_id,)).fetchone()
        binding = {"schema_version": "private-domain-common-fixture-binding/v1", "evidence_kind": MODE,
                   "common_plan_id": graph.common_plan_id, "common_run_id": graph.common_run_id,
                   "target_label": TARGET, "attempt": 1, "proof_digest": _sha(raw)}
        binding_raw = canonical_bytes(binding)
        packet = {"schema_version": "private-domain-completed-fixture-import/v1", "evidence_kind": MODE,
                  "proof_digest": _sha(raw), "execution_authority": False}
        packet_raw = canonical_bytes(packet)
        imported = digest(packet_raw)
        scope = "synthetic-domain-scope-" + digest(canonical_bytes([self.authority.marker["instance_id"], graph.offer_id]))
        parents = []
        for table, value, field in (("common_offer_history_coverage", expected["budget"], "coverage"),
                                    ("common_standing_policy_versions", packet, "policy")):
            source = db.execute(f"SELECT * FROM {table} WHERE scope_key=? AND generation=1", (scope,)).fetchone()
            encoded = canonical_bytes(value)
            parents.append(bool(source and source[field + "_json"].encode() == encoded
                                and source[field + "_digest"] == digest(encoded) and source["import_digest"] == imported))
        imported_row = db.execute("SELECT * FROM common_authority_imports WHERE import_digest=?", (imported,)).fetchone()
        event = db.execute("SELECT * FROM common_write_attempt_events WHERE reservation_id=? AND state='VERIFIED'", (common_reservation_id,)).fetchone()
        if (row["proof_json"].encode() != raw or row["proof_digest"] != digest(raw)
                or not reservation or reservation["plan_id"] != graph.common_plan_id
                or reservation["run_id"] != graph.common_run_id or reservation["target_label"] != TARGET
                or reservation["attempt"] != 1 or reservation["state"] != "VERIFIED"
                or reservation["scope_key"] != scope or reservation["coverage_generation"] != 1
                or reservation["policy_generation"] != 1 or reservation["authority_digest"] != imported
                or reservation["binding_json"].encode() != binding_raw
                or reservation["binding_digest"] != digest(binding_raw) or not all(parents)
                or not imported_row or imported_row["packet_json"].encode() != packet_raw
                or not event or event["evidence_json"].encode() != packet_raw
                or event["evidence_digest"] != digest(packet_raw)):
            raise DomainReviewBlocked("DOMAIN_FIXTURE_PROOF_CHANGED")
        return DomainCommonProof(common_reservation_id, marketplace_plan_id, graph.common_plan_id,
                                 graph.common_run_id, expected["graph_digest"], graph.common_readback_digest,
                                 expected["budget_digest"], MODE)
