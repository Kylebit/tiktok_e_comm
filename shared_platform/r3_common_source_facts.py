"""Native service source extraction, not COMMON write/approval authority.

Only the caller's existing ReleaseStore SQLite snapshot is read. Stored
normalized readbacks are preserved byte-for-byte and never relabelled as raw
official responses. This bounded reader does not infer complete external
history, a budget baseline, or the lifetime of a publication cycle.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import sqlite3
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from modules.miaoshou.client import CommonDetailObservation

from shared_platform.r3_frozen_review_producer import (
    DomainReviewBlocked, DomainReviewGraph, _bytes, read_stored_domain_graph,
)

COMMON = "miaoshou:COMMON"
_REQUIRED_COLUMNS = {
    "release_plans": {"plan_id", "product_id", "payload_json", "payload_digest", "target_labels_json"},
    "release_runs": {"run_id", "plan_id", "approval_id", "status", "created_at", "updated_at"},
    "release_target_runs": {"run_id", "target_label", "idempotency_key", "status", "attempts", "external_id"},
    "release_target_readbacks": {"run_id", "target_label", "evidence_json", "evidence_digest", "verified_at"},
    "release_target_failure_events": {"run_id", "target_label", "attempt", "evidence_json", "evidence_digest"},
    "release_target_submissions": {"run_id", "target_label", "evidence_json", "evidence_digest",
                                    "verification_evidence_json", "verification_evidence_digest"},
}


@dataclass(frozen=True)
class CommonSourceRecord:
    table: str
    identity: tuple[str, ...]
    record_bytes: bytes
    evidence_bytes: bytes | None = None
    evidence_digest: str | None = None
    official_response_bytes: bytes | None = None
    verification_evidence_bytes: bytes | None = None
    transport_observation: "CommonDetailObservation | None" = None
    comparison_bytes: bytes | None = None
    lineage_bytes: bytes | None = None


@dataclass(frozen=True)
class CommonPreparationSourceGraph:
    """Retained preparation identity, without a final review/success graph."""
    offer_id: str
    common_plan_id: str
    common_payload_bytes: bytes
    common_payload_digest: str
    round1_snapshot_digest: str
    round2_identity_bytes: bytes
    targets: tuple[str, ...]
    marketplace_plan_id: None = None
    common_run_id: None = None


@dataclass(frozen=True)
class CommonSourceFacts:
    graph: DomainReviewGraph | CommonPreparationSourceGraph
    records: tuple[CommonSourceRecord, ...]
    common_plan_ids: tuple[str, ...]
    unstarted_common_plan_ids: tuple[str, ...]
    source_coverage: str = "LOCAL_RETAINED_ONLY"
    official_provenance: str = "UNKNOWN"
    budget_status: str = "UNKNOWN"
    evidence_kind: str = "LOCAL_RETAINED_COMMON_SOURCE_FACTS"
    execution_authority: bool = False


def _json_bytes(raw, expected_digest, code):
    if type(raw) is not str or type(expected_digest) is not str:
        raise DomainReviewBlocked(code)
    encoded = raw.encode("utf-8")
    try:
        if (hashlib.sha256(encoded).hexdigest() != expected_digest
                or _bytes(json.loads(raw)) != encoded):
            raise DomainReviewBlocked(code)
    except (TypeError, ValueError):
        raise DomainReviewBlocked(code) from None
    return encoded


def _source_graph(db, plan_id, *, native_reader=None):
    """Read a genuine COMMON producer plan before its future marketplace run.

    Only the existing compiler's immutable plan identity is accepted here.
    Other origins retain the complete stored marketplace graph validation.
    The returned COMMON context does not supply approval/readback authority.
    """
    row = db.execute("SELECT * FROM release_plans WHERE plan_id=?", (plan_id,)).fetchone()
    if row is None:
        return read_stored_domain_graph(db, plan_id, native_reader=native_reader)
    try:
        payload = json.loads(row["payload_json"])
        labels = json.loads(row["target_labels_json"])
        if type(payload) is not dict or type(labels) is not list:
            raise ValueError("invalid stored origin")
        binding = payload.get("r3_stage_binding")
        common_origin = (COMMON in labels or payload.get("targets") == [COMMON]
                         or type(binding) is dict and binding.get("schema_version") == "r3-common-stage/v1")
    except (TypeError, ValueError):
        # Preserve the original full-graph malformed-origin error contract.
        return read_stored_domain_graph(db, plan_id, native_reader=native_reader)
    if not common_origin:
        return read_stored_domain_graph(db, plan_id, native_reader=native_reader)
    raw = _json_bytes(row["payload_json"], row["payload_digest"],
                      "COMMON_SOURCE_ORIGIN_BYTES_CHANGED")
    try:
        from shared_platform.publication_r3_image_bridge import common_stage_plan_id

        offer = row["product_id"]
        if (type(plan_id) is not str or labels != [COMMON] or type(binding) is not dict
                or payload.get("plan_id") != plan_id
                or common_stage_plan_id(payload, offer_id=offer) != plan_id):
            raise ValueError("COMMON producer identity differs from stored origin")
        targets = binding["marketplace_targets"]
        if (type(targets) is not list or not targets
                or any(type(target) is not str or not target for target in targets)
                or len(set(targets)) != len(targets)
                or any(target.startswith('miaoshou:') and target != COMMON for target in targets)):
            raise ValueError("COMMON marketplace target identity")
        if COMMON in targets or binding.get("native_preparation_source") is not None:
            # The native producer retains the complete immutable R1 scope,
            # including its separate technical COMMON member. Only after the
            # original stored preparation/source/snapshot/root has been fully
            # revalidated may this reader derive the market-only graph.
            from shared_platform.native_common_budget_facts import _validate_plan_source
            source = _validate_plan_source(db, payload)
            if source is None or source["targets"] != sorted(targets):
                raise ValueError("COMMON native complete target identity")
            targets = [target for target in targets if target != COMMON]
            if not targets:
                raise ValueError("COMMON marketplace target identity")
        return CommonPreparationSourceGraph(
            offer, plan_id, raw, row["payload_digest"], binding["round1_snapshot_digest"],
            _bytes(binding["r2_identity"]), tuple(targets))
    except (TypeError, ValueError, KeyError):
        raise DomainReviewBlocked("COMMON_SOURCE_ORIGIN_IDENTITY_INVALID") from None


def _native_transport_fields(store, db, row, evidence):
    """Revalidate the retained packet inside this existing SQLite snapshot.

    A historical event owns its attempt; a later target attempt cannot change
    that lineage. Raw transport bytes remain observations, never authority.
    """
    if type(evidence) is not dict or "native_common_observation" not in evidence:
        return None, None, None
    from shared_platform.release_store import ImmutableReleaseError
    try:
        import base64
        from modules.miaoshou.client import CommonDetailObservation

        if row["target_label"] != COMMON or type(evidence.get("stored_common_lineage")) is not dict:
            raise ValueError("missing native lineage")
        target = db.execute("SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?",
                            (row["run_id"], COMMON)).fetchone()
        if target is None:
            raise ValueError("missing native target")
        target = dict(target)
        attempt = row["attempt"] if "attempt" in row.keys() else target["attempts"]
        if type(attempt) is not int or not 1 <= attempt <= target["attempts"]:
            raise ValueError("native event attempt")
        target["attempts"] = attempt
        # This existing validator only SELECTs the actual run/plan and verifies
        # inner receipt, comparison digest and exact stored lineage. Requiring
        # the lineage above prevents its store-time enrichment on legacy rows.
        checked = store._validated_common_observation(db, target, evidence)
        if checked != evidence:
            raise ValueError("retained native facts changed")
        packet = evidence["native_common_observation"]
        observed = CommonDetailObservation(
            detail_id=packet["detail_id"], endpoint=packet["endpoint"],
            credential_scope_digest=packet["credential_scope_digest"],
            wire_bytes=base64.b64decode(packet["wire_base64"], validate=True),
            business_bytes=base64.b64decode(packet["business_base64"], validate=True),
            content_encoding=packet["content_encoding"], http_status=packet["http_status"])
        comparison = {key: value for key, value in evidence.items()
                      if key not in {"native_common_observation", "stored_common_lineage"}}
        return observed, _bytes(comparison), _bytes(evidence["stored_common_lineage"])
    except (ValueError, TypeError, KeyError, AttributeError, ImmutableReleaseError):
        raise DomainReviewBlocked("COMMON_SOURCE_NATIVE_OBSERVATION_INVALID") from None


def _record(table, row, identity, *, json_field=None, digest_field=None, store=None, db=None):
    value = dict(row)
    evidence = None
    digest = None
    if json_field is not None:
        evidence = _json_bytes(value[json_field], value[digest_field],
                               "COMMON_SOURCE_HISTORY_BYTES_CHANGED")
        digest = value[digest_field]
    verification = None
    if "verification_evidence_json" in value:
        verification_raw = value["verification_evidence_json"]
        verification_digest = value["verification_evidence_digest"]
        if verification_raw is not None or verification_digest is not None:
            verification = _json_bytes(verification_raw, verification_digest,
                                       "COMMON_SOURCE_HISTORY_BYTES_CHANGED")
    transport, comparison, lineage = (None, None, None)
    if store is not None and evidence is not None:
        transport, comparison, lineage = _native_transport_fields(store, db, row, json.loads(evidence))
    return CommonSourceRecord(table, tuple(str(value[key]) for key in identity),
                              _bytes(value), evidence, digest, None, verification,
                              transport, comparison, lineage)


class NativeCommonSourceReader:
    """A service-owned same-store reader; constructing it grants no authority.

    There is intentionally no proof-import method, caller-supplied budget,
    observed-at input, capability flag, approval method or provider callback.
    A future admitted native proof reader must separately bind stable policy,
    cycle/coverage/provenance business identities. These technical records and
    their timestamps must not become the final-review/common-budget digest.
    """
    def __init__(self, store):
        from shared_platform.release_store import ReleaseStore
        if type(store) is not ReleaseStore:
            raise DomainReviewBlocked("COMMON_SOURCE_RELEASE_STORE_REQUIRED")
        self.store = store

    def validate_context(self, db):
        if not isinstance(db, sqlite3.Connection) or not db.in_transaction:
            raise DomainReviewBlocked("DOMAIN_SQLITE_SNAPSHOT_REQUIRED")
        paths = [row[2] for row in db.execute("PRAGMA database_list") if row[1] == "main"]
        if len(paths) != 1 or Path(paths[0]).resolve() != self.store.path.resolve():
            raise DomainReviewBlocked("COMMON_SOURCE_DATABASE_MISMATCH")
        for table, required in _REQUIRED_COLUMNS.items():
            if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                raise DomainReviewBlocked("COMMON_SOURCE_SCHEMA_REQUIRED")
            columns = {row[1] for row in db.execute('PRAGMA table_info("' + table + '")')}
            if not required <= columns:
                raise DomainReviewBlocked("COMMON_SOURCE_SCHEMA_REQUIRED")

    def read_source_facts(self, db, marketplace_plan_id):
        self.validate_context(db)
        graph = _source_graph(db, marketplace_plan_id, native_reader=self)
        records = []
        plan_ids = []
        unstarted = []
        for plan in db.execute("SELECT * FROM release_plans WHERE product_id=? ORDER BY plan_id",
                               (graph.offer_id,)):
            try:
                labels = json.loads(plan["target_labels_json"])
                if type(labels) is not list or any(type(label) is not str for label in labels):
                    raise ValueError("invalid target labels")
            except (TypeError, ValueError):
                raise DomainReviewBlocked("COMMON_SOURCE_HISTORY_IDENTITY_INVALID") from None
            # Preserve a COMMON row even if its retained plan membership is
            # contradictory; do not erase it from later coverage evaluation.
            orphan = db.execute("SELECT 1 FROM release_runs r JOIN release_target_runs t ON t.run_id=r.run_id "
                                "WHERE r.plan_id=? AND t.target_label=? LIMIT 1",
                                (plan["plan_id"], COMMON)).fetchone()
            if COMMON not in labels and orphan is None:
                continue
            raw = _json_bytes(plan["payload_json"], plan["payload_digest"],
                              "COMMON_SOURCE_HISTORY_BYTES_CHANGED")
            payload = json.loads(raw)
            if str(payload.get("product_id")) != graph.offer_id:
                raise DomainReviewBlocked("COMMON_SOURCE_HISTORY_IDENTITY_INVALID")
            plan_ids.append(plan["plan_id"])
            records.append(_record("release_plans", plan, ("plan_id",),
                                   json_field="payload_json", digest_field="payload_digest"))
            runs = list(db.execute("SELECT * FROM release_runs WHERE plan_id=? ORDER BY run_id",
                                   (plan["plan_id"],)))
            if not runs:
                unstarted.append(plan["plan_id"])
            for run in runs:
                # Preserve technical RUN status/updated_at as source facts.
                # These aggregate values may change because another member
                # ran and are expressly not review or budget digest inputs.
                records.append(_record("release_runs", run, ("run_id",)))
                for target in db.execute("SELECT * FROM release_target_runs WHERE run_id=? AND target_label=?",
                                         (run["run_id"], COMMON)):
                    records.append(_record("release_target_runs", target, ("run_id", "target_label")))
                for table, identity, order in (
                    ("release_target_readbacks", ("run_id", "target_label"), "target_label"),
                    ("release_target_failure_events", ("run_id", "target_label", "attempt"), "attempt"),
                    ("release_target_submissions", ("run_id", "target_label"), "target_label"),
                ):
                    # The table/order names are a fixed internal allowlist.
                    for row in db.execute("SELECT * FROM " + table +
                                          " WHERE run_id=? AND target_label=? ORDER BY " + order,
                                          (run["run_id"], COMMON)):
                        records.append(_record(table, row, identity,
                                               json_field="evidence_json", digest_field="evidence_digest",
                                               store=self.store, db=db))
        return CommonSourceFacts(graph, tuple(records), tuple(plan_ids), tuple(unstarted))

    def read_completed_baseline(self, db, common_plan_id, common_run_id):
        """Useful retained baseline facts; no second COMMON approval or write."""
        from modules.products import server
        from shared_platform.native_common_baseline_source import inspect_service_baseline
        return inspect_service_baseline(server._service_common_baseline_reader(self.store),
            db, common_plan_id, common_run_id)

    def read_verified(self, db, common_reservation_id, marketplace_plan_id):
        # This is the real source seam, not a fake official/budget producer.
        # Validate the graph and retained bytes before diagnosing the absent
        # installed source authority; do not initialize or consume anything.
        self.read_source_facts(db, marketplace_plan_id)
        raise DomainReviewBlocked("COMMON_TRUSTED_BUDGET_AND_OFFICIAL_READBACK_REQUIRED")
