"""Private-only COMMON authority import and one-transaction attempt ledger.

Nothing here is connected to HTTP, workers, providers or default ReleaseStore.
Reviewed synthetic attachments exercise the future producer/consumer boundary;
they never establish an official historical baseline or dispatch authority.
Schema migration is explicit and additive in the *same* private ReleaseStore.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time
import uuid


MODE = "SYNTHETIC_TEST_ONLY"
TARGET = "miaoshou:COMMON"
OPERATIONS = frozenset({"COMMON_EDIT", "COMMON_CLAIM", "COMMON_FETCH"})
SCHEMA_VERSION = "common-private-authority/v1"
MARKER = "PRIVATE_COMMON_AUTHORITY.json"
MAX_ATTACHMENT_BYTES = 2 * 1024 * 1024


class CommonAuthorityBlocked(ValueError):
    """No new reservation/consumption was committed by a rejected operation."""


def canonical_bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _fail(code):
    raise CommonAuthorityBlocked(code)


def _text(value, field):
    if (type(value) is not str or not value or value != value.strip()
            or len(value) > 256 or any(ord(c) < 32 for c in value)):
        _fail("INVALID_" + field.upper())
    return value


def _integer(value, field, minimum=0):
    if type(value) is not int or value < minimum:
        _fail("INVALID_" + field.upper())
    return value


def _keys(value, required):
    if type(value) is not dict or set(value) != set(required):
        _fail("IMPORT_FIELDS_INVALID")


def _identity(value):
    _keys(value, {"tenant_id", "account_id", "offer_id", "common_item_id"})
    return {k: _text(v, k) for k, v in value.items()}


def _scope(identity):
    # The budget belongs to the Offer/account, not a recreatable COMMON item.
    return digest(canonical_bytes({k: identity[k] for k in ("tenant_id", "account_id", "offer_id")}))


def _json(raw):
    if type(raw) is not bytes or not raw or len(raw) > MAX_ATTACHMENT_BYTES:
        _fail("IMPORT_BYTES_INVALID")

    def pairs(items):
        result = {}
        for k, v in items:
            if k in result:
                _fail("DUPLICATE_JSON_KEY")
            result[k] = v
        return result

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=pairs,
                          parse_constant=lambda _: _fail("NONFINITE_JSON"))
    except (UnicodeError, json.JSONDecodeError):
        _fail("IMPORT_JSON_INVALID")


@dataclass(frozen=True)
class ReviewedPrivateImport:
    """A bounded local review attestation, never a production trust token.

    The expected digest/review reference come from an independently reviewed
    private fixture, not an HTTP payload. Production provenance is absent.
    """
    raw_packet: bytes
    attachments: tuple[tuple[str, bytes], ...]
    expected_packet_digest: str
    review_ref: str


def review_private_import(raw_packet, attachments, *, expected_packet_digest,
                          review_ref):
    _text(review_ref, "review_ref")
    if (type(expected_packet_digest) is not str
            or digest(raw_packet) != expected_packet_digest):
        _fail("REVIEWED_PACKET_DIGEST_MISMATCH")
    if type(attachments) is not dict or len(attachments) > 16:
        _fail("ATTACHMENT_SET_INVALID")
    reviewed = ReviewedPrivateImport(raw_packet, tuple(sorted(attachments.items())),
                                     expected_packet_digest, review_ref)
    _validated_import(reviewed)
    return reviewed


def _validated_import(reviewed):
    if type(reviewed) is not ReviewedPrivateImport:
        _fail("PRIVATE_REVIEW_ATTESTATION_REQUIRED")
    if digest(reviewed.raw_packet) != reviewed.expected_packet_digest:
        _fail("REVIEWED_PACKET_DIGEST_MISMATCH")
    _text(reviewed.review_ref, "review_ref")
    packet = _json(reviewed.raw_packet)
    _keys(packet, {"schema_version", "evidence_kind", "identity",
                  "coverage_ref", "coverage_generation", "policy_ref",
                  "attachment_manifest"})
    if (packet["schema_version"] != "common-authority-import/v1"
            or packet["evidence_kind"] != MODE):
        _fail("REAL_SOURCE_PRODUCER_NOT_IMPLEMENTED")
    identity = _identity(packet["identity"])
    generation = _integer(packet["coverage_generation"], "coverage_generation", 1)
    attachments = dict(reviewed.attachments)
    manifest = packet["attachment_manifest"]
    if (type(manifest) is not dict or not manifest
            or set(manifest) != set(attachments)):
        _fail("ATTACHMENT_SET_INVALID")
    for ref, raw in attachments.items():
        _text(ref, "attachment_ref")
        if (type(raw) is not bytes or not raw or len(raw) > MAX_ATTACHMENT_BYTES
                or digest(raw) != manifest[ref]):
            _fail("ATTACHMENT_BYTES_MISMATCH")
    if sum(map(len, attachments.values())) > 4 * MAX_ATTACHMENT_BYTES:
        _fail("ATTACHMENT_BUDGET_EXCEEDED")
    try:
        history = _json(attachments[packet["coverage_ref"]])
        policy = _json(attachments[packet["policy_ref"]])
    except (KeyError, TypeError):
        _fail("SOURCE_ATTACHMENT_MISSING")
    _keys(history, {"schema_version", "evidence_kind", "identity", "pages",
                    "declared_event_count", "retention_complete", "gaps",
                    "lifecycle_start_epoch", "covered_through_epoch"})
    if (history["schema_version"] != "common-history-fixture/v1"
            or history["evidence_kind"] != MODE or _identity(history["identity"]) != identity):
        _fail("HISTORY_SOURCE_IDENTITY_INVALID")
    start = _integer(history["lifecycle_start_epoch"], "lifecycle_start_epoch")
    through = _integer(history["covered_through_epoch"], "covered_through_epoch")
    if through < start or type(history["retention_complete"]) is not bool:
        _fail("HISTORY_COVERAGE_INVALID")
    if type(history["gaps"]) is not list or any(type(v) is not str for v in history["gaps"]):
        _fail("HISTORY_GAPS_INVALID")
    pages = history["pages"]
    if type(pages) is not list or not pages or len(pages) > 1000:
        _fail("HISTORY_PAGES_INVALID")
    events, seen, cursor = [], set(), ""
    terminal = False
    for index, page in enumerate(pages):
        _keys(page, {"page_number", "cursor", "next_cursor", "events"})
        if (type(page["page_number"]) is not int or page["page_number"] != index + 1
                or page["cursor"] != cursor or type(page["events"]) is not list
                or (index != len(pages) - 1 and not page["next_cursor"])):
            _fail("HISTORY_PAGE_CHAIN_INVALID")
        next_cursor = page["next_cursor"]
        if next_cursor is not None and type(next_cursor) is not str:
            _fail("HISTORY_PAGE_CHAIN_INVALID")
        cursor = next_cursor
        terminal = index == len(pages) - 1 and cursor is None
        for event in page["events"]:
            _keys(event, {"event_id", "operation_class", "outcome", "legacy_attempt"})
            event_id = _text(event["event_id"], "event_id")
            if event_id in seen or event["operation_class"] not in OPERATIONS:
                _fail("HISTORY_EVENT_CONFLICT")
            if event["outcome"] not in {"CONFIRMED", "UNKNOWN", "PROVEN_NOT_DISPATCHED"}:
                _fail("HISTORY_OUTCOME_INVALID")
            seen.add(event_id)
            legacy = event["legacy_attempt"]
            if legacy is not None:
                _keys(legacy, {"run_id", "attempt"})
                _text(legacy["run_id"], "run_id")
                _integer(legacy["attempt"], "attempt", 1)
            events.append(event)
    if _integer(history["declared_event_count"], "declared_event_count") != len(events):
        _fail("HISTORY_EVENT_COUNT_MISMATCH")
    legacy_ids = [(e["legacy_attempt"]["run_id"], e["legacy_attempt"]["attempt"])
                  for e in events if e["legacy_attempt"] is not None]
    if len(legacy_ids) != len(set(legacy_ids)):
        _fail("HISTORY_LEGACY_ATTEMPT_CONFLICT")
    _keys(policy, {"schema_version", "evidence_kind", "identity", "generation",
                   "parent_generation", "operation_classes", "maximum_attempts",
                   "history_count_rules", "reservation_count_rules",
                   "effective_at_epoch", "expires_at_epoch", "revoked",
                   "approval_evidence_ref"})
    if (policy["schema_version"] != "common-policy-fixture/v1"
            or policy["evidence_kind"] != MODE or _identity(policy["identity"]) != identity):
        _fail("POLICY_SOURCE_IDENTITY_INVALID")
    pg = _integer(policy["generation"], "policy_generation", 1)
    if _integer(policy["parent_generation"], "parent_generation") != pg - 1:
        _fail("POLICY_PARENT_INVALID")
    ops = policy["operation_classes"]
    if (type(ops) is not list or not ops or any(type(v) is not str or v not in OPERATIONS for v in ops)
            or len(ops) != len(set(ops))):
        _fail("POLICY_OPERATION_CLASS_INVALID")
    _integer(policy["maximum_attempts"], "maximum_attempts")
    if (any(type(rules) is not dict or any(type(v) is not int for v in rules.values())
            for rules in (policy["history_count_rules"], policy["reservation_count_rules"]))
            or policy["history_count_rules"] != {"CONFIRMED": 1, "UNKNOWN": 1, "PROVEN_NOT_DISPATCHED": 0}
            or policy["reservation_count_rules"] != {"RESERVED": 1, "UNKNOWN": 1,
                                                      "VERIFIED": 1, "PROVEN_NOT_DISPATCHED": 1}):
        _fail("POLICY_COUNT_RULES_INVALID")
    effective = _integer(policy["effective_at_epoch"], "effective_at_epoch")
    expiry = _integer(policy["expires_at_epoch"], "expires_at_epoch")
    if expiry <= effective or type(policy["revoked"]) is not bool:
        _fail("POLICY_LIFETIME_INVALID")
    try:
        approval = _json(attachments[policy["approval_evidence_ref"]])
    except (KeyError, TypeError):
        _fail("POLICY_APPROVAL_ATTACHMENT_MISSING")
    _keys(approval, {"schema_version", "evidence_kind", "identity", "policy_sha256", "approved"})
    if (approval["schema_version"] != "common-approval-fixture/v1"
            or approval["evidence_kind"] != MODE or approval["approved"] is not True
            or _identity(approval["identity"]) != identity
            or approval["policy_sha256"] != digest(attachments[packet["policy_ref"]])):
        _fail("POLICY_APPROVAL_BINDING_INVALID")
    coverage = {"identity": identity, "generation": generation,
                "source_sha256": digest(attachments[packet["coverage_ref"]]),
                "history": history, "events": events,
                "complete": terminal and history["retention_complete"] and not history["gaps"],
                "baseline_count": sum(policy["history_count_rules"][e["outcome"]] for e in events)}
    return packet, identity, coverage, policy


_TABLES = [
    """CREATE TABLE common_authority_schema_version (
        version TEXT PRIMARY KEY, private_instance TEXT NOT NULL)""",
    """CREATE TABLE common_authority_imports (
        import_digest TEXT PRIMARY KEY, packet_json TEXT NOT NULL,
        review_ref TEXT NOT NULL, imported_at_epoch INTEGER NOT NULL)""",
    """CREATE TABLE common_authority_attachments (
        import_digest TEXT NOT NULL, reference TEXT NOT NULL, raw_bytes BLOB NOT NULL,
        sha256 TEXT NOT NULL, PRIMARY KEY(import_digest, reference),
        FOREIGN KEY(import_digest) REFERENCES common_authority_imports(import_digest))""",
    """CREATE TABLE common_offer_history_coverage (
        scope_key TEXT NOT NULL, generation INTEGER NOT NULL CHECK(generation>0),
        coverage_digest TEXT NOT NULL, coverage_json TEXT NOT NULL, import_digest TEXT NOT NULL,
        PRIMARY KEY(scope_key,generation),
        FOREIGN KEY(import_digest) REFERENCES common_authority_imports(import_digest))""",
    """CREATE TABLE common_standing_policy_versions (
        scope_key TEXT NOT NULL, generation INTEGER NOT NULL CHECK(generation>0),
        policy_digest TEXT NOT NULL, policy_json TEXT NOT NULL, import_digest TEXT NOT NULL,
        PRIMARY KEY(scope_key,generation),
        FOREIGN KEY(import_digest) REFERENCES common_authority_imports(import_digest))""",
    """CREATE TABLE common_write_attempt_reservations (
        reservation_id TEXT PRIMARY KEY, scope_key TEXT NOT NULL, plan_id TEXT NOT NULL,
        run_id TEXT NOT NULL, target_label TEXT NOT NULL CHECK(target_label='miaoshou:COMMON'),
        attempt INTEGER NOT NULL CHECK(attempt>0), binding_json TEXT NOT NULL,
        binding_digest TEXT NOT NULL, coverage_generation INTEGER NOT NULL,
        policy_generation INTEGER NOT NULL, authority_digest TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('RESERVED','UNKNOWN','VERIFIED','PROVEN_NOT_DISPATCHED')),
        created_at_epoch INTEGER NOT NULL, updated_at_epoch INTEGER NOT NULL,
        UNIQUE(run_id,target_label,attempt),
        FOREIGN KEY(plan_id) REFERENCES release_plans(plan_id),
        FOREIGN KEY(run_id,target_label) REFERENCES release_target_runs(run_id,target_label),
        FOREIGN KEY(scope_key,coverage_generation) REFERENCES common_offer_history_coverage(scope_key,generation),
        FOREIGN KEY(scope_key,policy_generation) REFERENCES common_standing_policy_versions(scope_key,generation))""",
    """CREATE TABLE common_write_attempt_events (
        event_id TEXT PRIMARY KEY, reservation_id TEXT NOT NULL,
        state TEXT NOT NULL, evidence_json TEXT NOT NULL, evidence_digest TEXT NOT NULL,
        created_at_epoch INTEGER NOT NULL,
        UNIQUE(reservation_id,state),
        FOREIGN KEY(reservation_id) REFERENCES common_write_attempt_reservations(reservation_id))""",
]
_IMMUTABLE = ["common_authority_schema_version", "common_authority_imports",
              "common_authority_attachments", "common_offer_history_coverage",
              "common_standing_policy_versions", "common_write_attempt_events"]


def _check_schema_complete(db):
    required = {statement.split()[2] for statement in _TABLES}
    required.update(f"{table}_no_{action}" for table in _IMMUTABLE for action in ("update", "delete"))
    required.update({"common_reservation_identity_immutable", "common_reservation_no_delete"})
    present = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type IN ('table','trigger')")}
    if not required <= present:
        _fail("COMMON_SCHEMA_INCOMPLETE")


def _safe_path(path):
    path = Path(path)
    if not path.is_absolute() or path.resolve() != path.absolute():
        _fail("PRIVATE_PATH_NOT_DIRECT_ABSOLUTE")
    for part in (path, *path.parents):
        if part.exists() and (part.is_symlink() or getattr(part.stat(), "st_file_attributes", 0) & 0x400):
            _fail("PRIVATE_REPARSE_PATH_REJECTED")
    return path


def _time_text(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")


class PrivateCommonAuthorityStore:
    """Explicit private facade; no default path or real-source acceptance."""
    def __init__(self, store, private_root):
        self.root = _safe_path(private_root)
        self.store = store
        if Path(store.path).absolute() != self.root / "release.db":
            _fail("PRIVATE_STORE_PATH_MISMATCH")
        self.marker = _json((self.root / MARKER).read_bytes())
        _keys(self.marker, {"schema_version", "evidence_kind", "instance_id", "created_at_epoch"})
        if self.marker["schema_version"] != SCHEMA_VERSION or self.marker["evidence_kind"] != MODE:
            _fail("PRIVATE_MARKER_INVALID")
        _text(self.marker["instance_id"], "instance_id")
        _integer(self.marker["created_at_epoch"], "created_at_epoch")
        self._root_id = self.root.stat().st_ino
        self._db_id = _safe_path(store.path).stat().st_ino

    @classmethod
    def create(cls, private_root):
        """Create a vacant explicit private root; never adopt an existing DB."""
        root = _safe_path(private_root)
        root.mkdir()  # atomic exclusive create; existing root always refused
        marker = {"schema_version": SCHEMA_VERSION, "evidence_kind": MODE,
                  "instance_id": uuid.uuid4().hex, "created_at_epoch": int(time.time())}
        with (root / MARKER).open("xb") as file:
            file.write(canonical_bytes(marker))
        from shared_platform.release_store import ReleaseStore
        store = ReleaseStore(root / "release.db")
        with store._transaction():
            pass  # explicit creation of the existing base ReleaseStore schema
        return cls(store, root)

    @classmethod
    def open(cls, private_root):
        from shared_platform.release_store import ReleaseStore
        root = _safe_path(private_root)
        return cls(ReleaseStore(root / "release.db"), root)

    def _guard(self):
        _safe_path(self.root)
        _safe_path(self.store.path)
        if (self.root.stat().st_ino != self._root_id or self.store.path.stat().st_ino != self._db_id
                or _json((self.root / MARKER).read_bytes()) != self.marker):
            _fail("PRIVATE_INSTANCE_CHANGED")

    @contextmanager
    def _transaction(self, *, readonly=False):
        self._guard()
        db = self.store._connect_readonly() if readonly else self.store._connect()
        try:
            db.execute("BEGIN" if readonly else "BEGIN IMMEDIATE")
            try:
                row = db.execute("SELECT * FROM common_authority_schema_version").fetchall()
            except sqlite3.OperationalError:
                _fail("COMMON_SCHEMA_NOT_EXPLICITLY_MIGRATED")
            if len(row) != 1 or row[0]["version"] != SCHEMA_VERSION or row[0]["private_instance"] != self.marker["instance_id"]:
                _fail("COMMON_SCHEMA_INSTANCE_MISMATCH")
            _check_schema_complete(db)
            yield db
            self._guard()
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def migrate(self):
        """Add only private COMMON tables, in one explicit transaction."""
        self._guard()
        db = self.store._connect()
        try:
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute("SELECT name FROM sqlite_master WHERE name LIKE 'common_%'").fetchall()
            if existing:
                try:
                    row = db.execute("SELECT * FROM common_authority_schema_version").fetchall()
                    if len(row) == 1 and tuple(row[0]) == (SCHEMA_VERSION, self.marker["instance_id"]):
                        _check_schema_complete(db)
                        db.rollback()
                        return {"status": "ALREADY_MIGRATED_PRIVATE", "execution_authority": False}
                except sqlite3.OperationalError:
                    pass
                _fail("COMMON_SCHEMA_COLLISION")
            for statement in _TABLES:
                db.execute(statement)
            for table in _IMMUTABLE:
                for action in ("UPDATE", "DELETE"):
                    db.execute(f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} BEGIN SELECT RAISE(ABORT,'immutable COMMON authority'); END")
            db.execute("""CREATE TRIGGER common_reservation_identity_immutable
                BEFORE UPDATE OF reservation_id,scope_key,plan_id,run_id,target_label,attempt,
                    binding_json,binding_digest,coverage_generation,policy_generation,authority_digest,created_at_epoch
                ON common_write_attempt_reservations BEGIN SELECT RAISE(ABORT,'immutable COMMON reservation'); END""")
            db.execute("""CREATE TRIGGER common_reservation_no_delete BEFORE DELETE
                ON common_write_attempt_reservations BEGIN SELECT RAISE(ABORT,'append-only COMMON reservation'); END""")
            db.execute("INSERT INTO common_authority_schema_version VALUES (?,?)", (SCHEMA_VERSION, self.marker["instance_id"]))
            self._guard()
            db.commit()
            return {"status": "MIGRATED_PRIVATE", "execution_authority": False}
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    def import_reviewed(self, reviewed):
        packet, identity, coverage, policy = _validated_import(reviewed)
        scope = _scope(identity)
        imported = reviewed.expected_packet_digest
        # Baseline may only cover pre-ledger history; post-ledger attempts are
        # counted from this reservation table, never double-counted or reset.
        if coverage["history"]["covered_through_epoch"] > self.marker["created_at_epoch"]:
            _fail("HISTORY_OVERLAPS_PRIVATE_LEDGER")
        with self._transaction() as db:
            old = db.execute("SELECT * FROM common_authority_imports WHERE import_digest=?", (imported,)).fetchone()
            if old:
                return self._import_receipt(scope, imported, created=False)
            for table, value, field in (("common_offer_history_coverage", coverage, "coverage"),
                                        ("common_standing_policy_versions", policy, "policy")):
                generation = value["generation"]
                latest = db.execute(f"SELECT * FROM {table} WHERE scope_key=? ORDER BY generation DESC LIMIT 1", (scope,)).fetchone()
                encoded = canonical_bytes(value).decode("utf-8")
                if latest:
                    if json.loads(latest[field + "_json"])["identity"] != identity:
                        _fail("COMMON_ITEM_IDENTITY_CHANGE_REQUIRES_RECONCILIATION")
                    if generation == latest["generation"]:
                        if latest[field + "_json"] != encoded:
                            _fail("AUTHORITY_GENERATION_CONTENT_CONFLICT")
                    elif generation != latest["generation"] + 1:
                        _fail("AUTHORITY_GENERATION_STALE_OR_GAPPED")
                    if field == "coverage":
                        before = json.loads(latest["coverage_json"])
                        if (before["history"]["covered_through_epoch"] != coverage["history"]["covered_through_epoch"]
                                or coverage["baseline_count"] < before["baseline_count"]):
                            _fail("HISTORY_BASELINE_CANNOT_RESET")
                elif generation != 1:
                    _fail("AUTHORITY_INITIAL_GENERATION_INVALID")
            now = int(time.time())
            db.execute("INSERT INTO common_authority_imports VALUES (?,?,?,?)",
                       (imported, reviewed.raw_packet.decode("utf-8"), reviewed.review_ref, now))
            for ref, raw in reviewed.attachments:
                db.execute("INSERT INTO common_authority_attachments VALUES (?,?,?,?)", (imported, ref, raw, digest(raw)))
            for table, value, field in (("common_offer_history_coverage", coverage, "coverage"),
                                        ("common_standing_policy_versions", policy, "policy")):
                encoded = canonical_bytes(value)
                db.execute(f"INSERT OR IGNORE INTO {table} VALUES (?,?,?,?,?)",
                           (scope, value["generation"], digest(encoded), encoded.decode("utf-8"), imported))
            return self._import_receipt(scope, imported, created=True)

    @staticmethod
    def _import_receipt(scope, imported, *, created):
        return {"status": "IMPORTED_REVIEWED_PRIVATE_FIXTURE", "scope_key": scope,
                "import_digest": imported, "created": created, "coverage_kind": MODE,
                "execution_authority": False, "final_review_available": False,
                "external_writes_performed": []}

    def _authority(self, db, identity):
        scope = _scope(identity)
        coverage_row = db.execute("SELECT * FROM common_offer_history_coverage WHERE scope_key=? ORDER BY generation DESC LIMIT 1", (scope,)).fetchone()
        policy_row = db.execute("SELECT * FROM common_standing_policy_versions WHERE scope_key=? ORDER BY generation DESC LIMIT 1", (scope,)).fetchone()
        if not coverage_row or not policy_row:
            _fail("COMMON_AUTHORITY_SOURCE_MISSING")
        coverage = json.loads(coverage_row["coverage_json"])
        policy = json.loads(policy_row["policy_json"])
        if (digest(canonical_bytes(coverage)) != coverage_row["coverage_digest"]
                or digest(canonical_bytes(policy)) != policy_row["policy_digest"]):
            _fail("COMMON_AUTHORITY_BYTES_INVALID")
        if coverage["identity"] != identity or policy["identity"] != identity:
            _fail("COMMON_AUTHORITY_PHYSICAL_IDENTITY_MISMATCH")
        if not coverage["complete"] or any(e["outcome"] == "UNKNOWN" for e in coverage["events"]):
            _fail("COMMON_HISTORY_INCOMPLETE_OR_UNKNOWN")
        now = int(time.time())
        if policy["revoked"] or not policy["effective_at_epoch"] <= now < policy["expires_at_epoch"]:
            _fail("COMMON_POLICY_EXPIRED_OR_REVOKED")
        # Include stable authority generations/digests, NOT reservation state.
        binding = {"identity": identity, "coverage_generation": coverage["generation"],
                   "coverage_digest": coverage_row["coverage_digest"],
                   "policy_generation": policy["generation"], "policy_digest": policy_row["policy_digest"]}
        return scope, coverage, policy, digest(canonical_bytes(binding))

    def _binding(self, db, request, mutation_bytes):
        _keys(request, {"plan_id", "run_id", "attempt", "product_revision",
                        "payload_digest", "coverage_generation", "policy_generation"})
        row = db.execute("""SELECT p.*,r.status AS run_status,t.status AS target_status,t.attempts
            FROM release_plans p JOIN release_runs r ON r.plan_id=p.plan_id
            JOIN release_target_runs t ON t.run_id=r.run_id
            WHERE p.plan_id=? AND r.run_id=? AND t.target_label=?""",
                         (request["plan_id"], request["run_id"], TARGET)).fetchone()
        if not row:
            _fail("COMMON_PLAN_RUN_BINDING_MISMATCH")
        raw = row["payload_json"].encode("utf-8")
        payload = _json(raw)
        if digest(raw) != row["payload_digest"] or canonical_bytes(payload) != raw:
            _fail("COMMON_STORED_PAYLOAD_BYTES_INVALID")
        if (row["payload_digest"] != request["payload_digest"]
                or type(payload.get("product_revision")) is not int
                or payload["product_revision"] != request["product_revision"]
                or type(request["product_revision"]) is not int
                or payload.get("plan_id") != row["plan_id"] or payload.get("product_id") != row["product_id"]
                or payload.get("targets") != json.loads(row["target_labels_json"])):
            _fail("COMMON_EXPECTED_PAYLOAD_BINDING_MISMATCH")
        contract = payload.get("common_private_contract")
        _keys(contract, {"schema_version", "identity", "operation_class", "mutation"})
        if contract["schema_version"] != "common-private-plan/v1":
            _fail("COMMON_PRIVATE_CONTRACT_INVALID")
        identity = _identity(contract["identity"])
        if identity["offer_id"] != row["product_id"] or contract["operation_class"] not in OPERATIONS:
            _fail("COMMON_OPERATION_IDENTITY_INVALID")
        if type(contract["mutation"]) is not dict or type(mutation_bytes) is not bytes or canonical_bytes(contract["mutation"]) != mutation_bytes:
            _fail("COMMON_ACTUAL_MUTATION_BYTES_MISMATCH")
        attempt = _integer(request["attempt"], "attempt", 1)
        scope, coverage, policy, authority = self._authority(db, identity)
        if (type(request["coverage_generation"]) is not int or type(request["policy_generation"]) is not int
                or request["coverage_generation"] != coverage["generation"]
                or request["policy_generation"] != policy["generation"]):
            _fail("COMMON_AUTHORITY_GENERATION_CHANGED")
        if contract["operation_class"] not in policy["operation_classes"]:
            _fail("COMMON_OPERATION_CLASS_NOT_ALLOWED")
        if row["status"] == "SUPERSEDED" or row["run_status"] == "SUPERSEDED":
            _fail("COMMON_PLAN_SUPERSEDED")
        binding = {**request, "identity": identity, "target_label": TARGET,
                   "operation_class": contract["operation_class"],
                   "mutation_sha256": digest(mutation_bytes), "targets": payload["targets"]}
        return row, scope, coverage, policy, authority, binding, attempt

    def _history_guard(self, db, scope, coverage, offer_id):
        mapped = {(e["legacy_attempt"]["run_id"], e["legacy_attempt"]["attempt"])
                  for e in coverage["events"] if e["legacy_attempt"] is not None}
        for row in db.execute("""SELECT r.run_id,t.attempts FROM release_runs r
            JOIN release_plans p ON p.plan_id=r.plan_id
            JOIN release_target_runs t ON t.run_id=r.run_id
            WHERE p.product_id=? AND t.target_label=?""", (offer_id, TARGET)):
            covered = {v["attempt"] for v in db.execute("SELECT attempt FROM common_write_attempt_reservations WHERE run_id=? AND scope_key=?", (row["run_id"], scope))}
            for attempt in range(1, row["attempts"] + 1):
                if attempt not in covered and (row["run_id"], attempt) not in mapped:
                    _fail("COMMON_UNRESERVED_LEGACY_ATTEMPT_UNKNOWN")

    @staticmethod
    def _receipt(row, *, created=False, consumed=False):
        return {**dict(row), "created": created, "consumed": consumed,
                "private_contract_only": True, "dispatch_allowed": False,
                "safe_to_retry": False, "execution_authority": False,
                "final_review_available": False, "external_writes_performed": []}

    def reserve(self, request, mutation_bytes, *, reservation_id):
        _text(reservation_id, "reservation_id")
        with self._transaction() as db:
            row, scope, coverage, policy, authority, binding, attempt = self._binding(db, request, mutation_bytes)
            encoded = canonical_bytes(binding).decode("utf-8")
            existing = db.execute("SELECT * FROM common_write_attempt_reservations WHERE reservation_id=? OR (run_id=? AND target_label=? AND attempt=?)",
                                  (reservation_id, request["run_id"], TARGET, attempt)).fetchall()
            if existing:
                if len(existing) != 1 or existing[0]["reservation_id"] != reservation_id or existing[0]["binding_json"] != encoded or existing[0]["authority_digest"] != authority:
                    _fail("COMMON_RESERVATION_REPLAY_CONFLICT")
                return self._receipt(existing[0])
            self._history_guard(db, scope, coverage, row["product_id"])
            if db.execute("SELECT 1 FROM common_write_attempt_reservations WHERE scope_key=? AND state IN ('RESERVED','UNKNOWN')", (scope,)).fetchone():
                _fail("COMMON_PRIOR_ATTEMPT_RECONCILIATION_REQUIRED")
            count = db.execute("SELECT COUNT(*) FROM common_write_attempt_reservations WHERE scope_key=?", (scope,)).fetchone()[0]
            if coverage["baseline_count"] + count >= policy["maximum_attempts"]:
                _fail("COMMON_OFFER_BUDGET_EXHAUSTED")
            if (row["target_status"] not in {"PENDING", "FAILED"} or row["attempts"] != attempt - 1
                    or row["run_status"] not in {"PENDING", "RUNNING", "PARTIAL_FAILED", "FAILED"}):
                _fail("COMMON_ATTEMPT_STATE_MISMATCH")
            now = int(time.time())
            db.execute("""INSERT INTO common_write_attempt_reservations VALUES (?,?,?,?,?,?,?,?,?,?,?,'RESERVED',?,?)""",
                       (reservation_id, scope, row["plan_id"], request["run_id"], TARGET,
                        attempt, encoded, digest(encoded.encode("utf-8")), coverage["generation"],
                        policy["generation"], authority, now, now))
            changed = db.execute("""UPDATE release_target_runs SET status='RUNNING',attempts=attempts+1,
                error=NULL,completed_at=NULL,updated_at=? WHERE run_id=? AND target_label=?
                AND attempts=? AND status IN ('PENDING','FAILED')""", (_time_text(now), request["run_id"], TARGET, attempt - 1))
            if changed.rowcount != 1:
                _fail("COMMON_TARGET_CLAIM_LOST")
            changed = db.execute("""UPDATE release_runs SET status='RUNNING',updated_at=?,completed_at=NULL
                WHERE run_id=? AND plan_id=? AND status IN ('PENDING','RUNNING','PARTIAL_FAILED','FAILED')""",
                                 (_time_text(now), request["run_id"], request["plan_id"]))
            if changed.rowcount != 1:
                _fail("COMMON_RUN_CLAIM_LOST")
            return self._receipt(db.execute("SELECT * FROM common_write_attempt_reservations WHERE reservation_id=?", (reservation_id,)).fetchone(), created=True)

    def consume_private(self, reservation_id, request, mutation_bytes):
        """CAS before a fake transport; commit UNKNOWN, never grant real dispatch."""
        with self._transaction() as db:
            row, _, _, _, authority, binding, attempt = self._binding(db, request, mutation_bytes)
            reservation = db.execute("SELECT * FROM common_write_attempt_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
            if (not reservation or reservation["binding_json"] != canonical_bytes(binding).decode("utf-8")
                    or reservation["authority_digest"] != authority):
                _fail("COMMON_CONSUME_BINDING_CHANGED")
            if reservation["state"] != "RESERVED":
                return self._receipt(reservation)
            if row["target_status"] != "RUNNING" or row["attempts"] != attempt:
                _fail("COMMON_RESERVED_TARGET_STATE_CHANGED")
            now = int(time.time())
            changed = db.execute("UPDATE common_write_attempt_reservations SET state='UNKNOWN',updated_at_epoch=? WHERE reservation_id=? AND state='RESERVED'", (now, reservation_id))
            if changed.rowcount != 1:
                _fail("COMMON_CONSUME_CAS_LOST")
            self._event(db, reservation_id, "UNKNOWN", {"reason": "PRIVATE_CONSUMED_BEFORE_FAKE_TRANSPORT"}, now)
            return self._receipt(db.execute("SELECT * FROM common_write_attempt_reservations WHERE reservation_id=?", (reservation_id,)).fetchone(), consumed=True)

    @staticmethod
    def _event(db, reservation_id, state, evidence, now):
        encoded = canonical_bytes(evidence).decode("utf-8")
        db.execute("INSERT INTO common_write_attempt_events VALUES (?,?,?,?,?,?)",
                   (digest(canonical_bytes([reservation_id, state])), reservation_id, state,
                    encoded, digest(encoded.encode("utf-8")), now))

    def verify_private_readback(self, reservation_id, request, mutation_bytes):
        """A VERIFIED flag alone never establishes a reusable receipt."""
        with self._transaction(readonly=True) as db:
            plan, _, _, _, authority, binding, attempt = self._binding(db, request, mutation_bytes)
            reservation = db.execute("SELECT * FROM common_write_attempt_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
            encoded_binding = canonical_bytes(binding)
            if (not reservation or reservation["state"] != "VERIFIED"
                    or reservation["binding_json"].encode("utf-8") != encoded_binding
                    or reservation["binding_digest"] != digest(encoded_binding)
                    or reservation["authority_digest"] != authority
                    or plan["target_status"] != "SUCCEEDED" or plan["attempts"] != attempt):
                _fail("COMMON_READBACK_BINDING_INVALID")
            target = db.execute("SELECT external_id FROM release_target_runs WHERE run_id=? AND target_label=?", (binding["run_id"], TARGET)).fetchone()
            receipt = db.execute("SELECT * FROM release_target_readbacks WHERE run_id=? AND target_label=?", (binding["run_id"], TARGET)).fetchone()
            event = db.execute("SELECT * FROM common_write_attempt_events WHERE reservation_id=? AND state='VERIFIED'", (reservation_id,)).fetchone()
            expected = {"schema_version": "common-private-readback/v1", "evidence_kind": MODE,
                        "identity": binding["identity"], "mutation": _json(mutation_bytes),
                        "external_id": binding["identity"]["common_item_id"],
                        "reservation_id": reservation_id, "outcome": "VERIFIED"}
            raw = canonical_bytes(expected)
            if (not receipt or not event or not target or target["external_id"] != expected["external_id"]
                    or any(row["evidence_json"].encode("utf-8") != raw
                           or row["evidence_digest"] != digest(raw) for row in (receipt, event))):
                _fail("COMMON_READBACK_PERSISTED_EVIDENCE_INVALID")
            return self._receipt(reservation)

    def reconcile_private(self, reservation_id, evidence):
        """Readback recovery only; UNKNOWN never creates another POST attempt."""
        _keys(evidence, {"schema_version", "evidence_kind", "identity", "mutation",
                         "external_id", "reservation_id", "outcome"})
        if (evidence["schema_version"] != "common-private-readback/v1" or evidence["evidence_kind"] != MODE
                or evidence["reservation_id"] != reservation_id or evidence["outcome"] not in {"VERIFIED", "PROVEN_NOT_DISPATCHED"}):
            _fail("COMMON_READBACK_PROVENANCE_INVALID")
        with self._transaction() as db:
            reservation = db.execute("SELECT * FROM common_write_attempt_reservations WHERE reservation_id=?", (reservation_id,)).fetchone()
            if not reservation:
                _fail("COMMON_RESERVATION_MISSING")
            binding = json.loads(reservation["binding_json"])
            state = evidence["outcome"]
            if (_identity(evidence["identity"]) != binding["identity"] or type(evidence["mutation"]) is not dict
                    or digest(canonical_bytes(evidence["mutation"])) != binding["mutation_sha256"]
                    or evidence["external_id"] != binding["identity"]["common_item_id"]):
                _fail("COMMON_READBACK_FIELD_MISMATCH")
            old = db.execute("SELECT * FROM common_write_attempt_events WHERE reservation_id=? AND state=?", (reservation_id, state)).fetchone()
            if old:
                if old["evidence_json"] != canonical_bytes(evidence).decode("utf-8"):
                    _fail("COMMON_READBACK_REPLAY_CONFLICT")
                return self._receipt(reservation)
            if reservation["state"] != "UNKNOWN":
                _fail("COMMON_RECONCILIATION_STATE_INVALID")
            target = db.execute("SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?", (reservation["run_id"], TARGET)).fetchone()
            if target["attempts"] != reservation["attempt"] or target["status"] != "RUNNING":
                _fail("COMMON_RECONCILIATION_TARGET_CHANGED")
            now = int(time.time())
            self._event(db, reservation_id, state, evidence, now)
            db.execute("UPDATE common_write_attempt_reservations SET state=?,updated_at_epoch=? WHERE reservation_id=? AND state='UNKNOWN'", (state, now, reservation_id))
            target_state = "SUCCEEDED" if state == "VERIFIED" else "FAILED"
            db.execute("UPDATE release_target_runs SET status=?,external_id=?,updated_at=?,completed_at=? WHERE run_id=? AND target_label=?",
                       (target_state, evidence["external_id"] if state == "VERIFIED" else None,
                        _time_text(now), _time_text(now), reservation["run_id"], TARGET))
            if state == "VERIFIED":
                raw = canonical_bytes(evidence)
                db.execute("INSERT INTO release_target_readbacks VALUES (?,?,?,?,?)", (reservation["run_id"], TARGET, raw.decode("utf-8"), digest(raw), _time_text(now)))
            self.store._refresh_run_status(db, reservation["run_id"], now=_time_text(now))
            return self._receipt(db.execute("SELECT * FROM common_write_attempt_reservations WHERE reservation_id=?", (reservation_id,)).fetchone())

    def inspect(self):
        with self._transaction(readonly=True) as db:
            return {"schema_version": SCHEMA_VERSION, "evidence_kind": MODE,
                    "imports": db.execute("SELECT COUNT(*) FROM common_authority_imports").fetchone()[0],
                    "reservations": [dict(r) for r in db.execute("SELECT * FROM common_write_attempt_reservations ORDER BY reservation_id")],
                    "execution_authority": False, "final_review_available": False,
                    "external_writes_performed": []}
