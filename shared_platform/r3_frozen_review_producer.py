"""Domain-owned original-page R3 review; no provider or approval capability.

The public default is closed. The existing private COMMON ledger can exercise
the full domain graph, but its synthetic readback is never official evidence.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from shared_platform.final_review_server_admission import FrozenReview


def _bytes(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(raw):
    return "sha256:" + hashlib.sha256(raw).hexdigest()


class DomainReviewBlocked(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class DomainFrozenCandidate:
    review: FrozenReview
    display_bytes: bytes
    manifest_bytes: bytes
    common_reservation_id: str
    evidence_kind: str = "DOMAIN_SOURCE_FROZEN_REVIEW"

    def descriptor(self):
        return {"schema_version": "local-operator-review/v1",
                "reservation_id": self.common_reservation_id,
                "marketplace_plan_id": self.review.plan_id,
                "offer_id": self.review.offer_id,
                "review_digest": self.review.digest(),
                "candidate_digest": self.review.candidate_digest,
                "evidence_kind": self.evidence_kind,
                "execution_authority": False}

    def manifest(self):
        return json.loads(self.manifest_bytes)


@dataclass(frozen=True)
class DomainReviewGraph:
    """Validated complete display, without a final-decision capability."""
    offer_id: str
    marketplace_plan_id: str
    common_plan_id: str
    common_run_id: str
    targets: tuple[str, ...]
    display_bytes: bytes
    manifest_bytes: bytes
    candidate_digest: str
    critical_content_digest: str
    round1_digest: str
    round2_digest: str
    common_readback_digest: str


def rebuild_domain_review_graph(common_plan, common_run, marketplace_plan, *, native_completion=None,
                                category_store=None, category_connection=None):
    """Pure service seam; input values must come from domain storage, not HTTP.

    Checksums alone are insufficient: the retained marketplace payload and its
    entire review are regenerated from the complete R1/R2 predecessor graph.
    This helper returns no FrozenReview and cannot record an operator decision.
    """
    from shared_platform.publication_r3_image_bridge import (
        build_marketplace_review_material, validate_r2_identity,
    )
    from shared_platform.release_store import preview_release_plan, PLAN_PENDING_APPROVAL, _target_labels
    try:
        payload = marketplace_plan["payload"]
        binding = payload["r3_marketplace_binding"]
        documents = binding["documents"]
        identity = validate_r2_identity(documents)
        r1 = documents["round1_snapshot"]
        targets = _target_labels([label for label in r1["canonical_targets"] if label != "miaoshou:COMMON"])
        if common_plan["status"] != "APPROVED":
            from shared_platform.native_common_retained_completion import RetainedCommonCompletion
            if (common_plan["status"] != PLAN_PENDING_APPROVAL or type(native_completion) is not RetainedCommonCompletion
                    or native_completion.plan_id != common_plan["plan_id"]
                    or native_completion.payload_digest != common_plan["payload_digest"]
                    or native_completion.offer_id != r1["offer_id"]
                    or native_completion.run_id != common_run["run_id"]
                    or native_completion.state not in {"CONFIRMED_WRITE", "READONLY_REUSE"}
                    or native_completion.readback_digest != binding["common_readback"]["evidence_digest"]):
                raise DomainReviewBlocked("COMMON_TECHNICAL_COMPLETION_UNPROVEN")
        if (marketplace_plan["plan_id"] == common_plan["plan_id"]
                or marketplace_plan["product_id"] != r1["offer_id"]
                or common_plan["product_id"] != r1["offer_id"]
                or marketplace_plan["status"] == "SUPERSEDED"
                or common_run["plan_id"] != common_plan["plan_id"]
                or binding["common_plan_id"] != common_plan["plan_id"]
                or binding["common_run_id"] != common_run["run_id"]
                or binding["common_payload_digest"] != common_plan["payload_digest"]
                or binding["r2_identity"] != identity
                or tuple(marketplace_plan["targets"]) != targets
                or tuple(payload["targets"]) != targets):
            raise DomainReviewBlocked("DOMAIN_PLAN_SOURCE_SCOPE_CHANGED")
        common_targets = common_run["targets"]
        if (len(common_targets) != 1
                or common_targets[0]["target_label"] != "miaoshou:COMMON"
                or common_targets[0]["status"] != "SUCCEEDED"
                or common_targets[0]["readback"] != binding["common_readback"]):
            raise DomainReviewBlocked("COMMON_OFFICIAL_FIELD_READBACK_UNPROVEN")
        material = build_marketplace_review_material(
            documents, common_plan, common_run, policy=binding["policy"],
            incidents=binding["incident_registry"],
            promotion_policy=payload.get("approved_postpublish_promotion_policy"),
            ozon_stock_decision=payload["product_facts"].get("ozon_stock_decision"),
            category_store=category_store, category_connection=category_connection)
        rebuilt = preview_release_plan(material["payload"])
        if (rebuilt["plan_id"] != marketplace_plan["plan_id"]
                or rebuilt["payload_digest"] != marketplace_plan["payload_digest"]
                or _bytes(rebuilt["payload"]) != _bytes(payload)):
            raise DomainReviewBlocked("DOMAIN_REVIEW_DISPLAY_OR_PAYLOAD_CHANGED")
        candidate = material["candidate"]
        manifest = candidate["review_manifest"]
        if (tuple(candidate["target_labels"]) != targets
                or tuple(row["target_label"] for row in manifest["targets"]) != targets
                or not manifest["variants"] or not manifest["copy_sets"]
                or not manifest["image_sets"]):
            raise DomainReviewBlocked("DOMAIN_REVIEW_COMPLETE_MATRIX_REQUIRED")
        manifest_material = {k: v for k, v in manifest.items() if k != "manifest_digest"}
        # publication_autopilot owns this manifest contract and emits a bare
        # SHA256, unlike R1/R2's prefixed digest contract. Do not rewrite it.
        if manifest["manifest_digest"] != hashlib.sha256(_bytes(manifest_material)).hexdigest():
            raise DomainReviewBlocked("DOMAIN_REVIEW_MANIFEST_BYTES_CHANGED")
        display = _bytes(candidate)
        critical = _bytes({"candidate": {k: v for k, v in candidate.items()
                                       if k not in {"candidate_digest", "snapshot_digest"}},
                           "documents": documents,
                           "common_readback": binding["common_readback"]})
        return DomainReviewGraph(
            r1["offer_id"], rebuilt["plan_id"], common_plan["plan_id"], common_run["run_id"],
            targets, display, _bytes(manifest), candidate["candidate_digest"], _sha(critical),
            r1["snapshot_digest"], identity["identity_digest"], _sha(_bytes(binding["common_readback"])))
    except DomainReviewBlocked:
        raise
    except (ValueError, TypeError, KeyError, IndexError) as error:
        raise DomainReviewBlocked("DOMAIN_SOURCE_GRAPH_INVALID: " + str(error)) from error


def read_stored_domain_graph(db, marketplace_plan_id, *, native_reader=None, category_store=None):
    """Reconstruct in the caller's existing SQLite snapshot, without writes."""
    if not db.in_transaction:
        raise DomainReviewBlocked("DOMAIN_SQLITE_SNAPSHOT_REQUIRED")
    from shared_platform.release_store import _plan_from_row, ReleaseStoreError
    def read_plan(plan_id):
        row = db.execute("SELECT * FROM release_plans WHERE plan_id=?", (plan_id,)).fetchone()
        if not row:
            raise DomainReviewBlocked("DOMAIN_RETAINED_PLAN_REQUIRED")
        raw = row["payload_json"].encode("utf-8")
        if hashlib.sha256(raw).hexdigest() != row["payload_digest"] or _bytes(json.loads(raw)) != raw:
            raise DomainReviewBlocked("DOMAIN_STORED_PLAN_BYTES_CHANGED")
        return _plan_from_row(row)
    try:
        market = read_plan(marketplace_plan_id)
        binding = market["payload"]["r3_marketplace_binding"]
        common = read_plan(binding["common_plan_id"])
        row = db.execute("SELECT * FROM release_runs WHERE run_id=? AND plan_id=?",
                         (binding["common_run_id"], common["plan_id"])).fetchone()
        if not row:
            raise DomainReviewBlocked("DOMAIN_RETAINED_COMMON_RUN_REQUIRED")
        run = dict(row)
        run["targets"] = []
        for target in db.execute("SELECT * FROM release_target_runs WHERE run_id=? ORDER BY target_label", (run["run_id"],)):
            value = dict(target)
            readback = db.execute("SELECT * FROM release_target_readbacks WHERE run_id=? AND target_label=?",
                                  (run["run_id"], value["target_label"])).fetchone()
            if readback:
                raw = readback["evidence_json"].encode("utf-8")
                if hashlib.sha256(raw).hexdigest() != readback["evidence_digest"] or _bytes(json.loads(raw)) != raw:
                    raise DomainReviewBlocked("DOMAIN_STORED_READBACK_BYTES_CHANGED")
                value["readback"] = {"evidence": json.loads(raw), "evidence_digest": readback["evidence_digest"],
                                     "verified_at": readback["verified_at"]}
            run["targets"].append(value)
        completion = None
        if native_reader is not None and common["status"] != "APPROVED":
            from shared_platform.native_common_retained_completion import read_retained_completion
            completion = read_retained_completion(native_reader, db, common["plan_id"], run["run_id"])
        return rebuild_domain_review_graph(common, run, market, native_completion=completion,
            category_store=native_reader.store if native_reader is not None else category_store,
            category_connection=db)
    except DomainReviewBlocked:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, ReleaseStoreError) as error:
        raise DomainReviewBlocked("DOMAIN_STORED_GRAPH_INVALID: " + str(error)) from error


class DomainFrozenReviewProducer:
    def __init__(self, authority=None):
        self.authority = authority

    def read_native_source_facts(self, db, marketplace_plan_id):
        """Native service source diagnostics; no FrozenReview or authority."""
        from shared_platform.r3_common_source_facts import NativeCommonSourceReader
        if type(self.authority) is not NativeCommonSourceReader:
            raise DomainReviewBlocked("COMMON_NATIVE_SOURCE_READER_REQUIRED")
        return self.authority.read_source_facts(db, marketplace_plan_id)

    def build(self, db, common_reservation_id, marketplace_plan_id):
        from shared_platform.common_offer_authority_store import PrivateCommonAuthorityStore
        from shared_platform.r3_domain_common_proof import PrivateDomainCommonProofReader
        from shared_platform.r3_common_source_facts import NativeCommonSourceReader
        if type(self.authority) is NativeCommonSourceReader:
            # This concrete reader supplies retained source facts only. It
            # cannot grant approval until native budget/official authority is
            # installed; normalized local readbacks are never raw API proof.
            self.authority.read_verified(db, common_reservation_id, marketplace_plan_id)
            raise DomainReviewBlocked("COMMON_TRUSTED_BUDGET_AND_OFFICIAL_READBACK_REQUIRED")
        # The native installed facade determines evidence kind. No request
        # body, approved_by, flag or caller-created FrozenReview is accepted.
        if type(self.authority) is not PrivateCommonAuthorityStore:
            raise DomainReviewBlocked("COMMON_TRUSTED_BUDGET_AND_OFFICIAL_READBACK_REQUIRED")
        reader = PrivateDomainCommonProofReader(self.authority)
        reader.validate_context(db)
        graph = read_stored_domain_graph(db, marketplace_plan_id, category_store=self.authority.store)
        proof = reader.read_verified(db, common_reservation_id, marketplace_plan_id, graph)
        review = FrozenReview(
            graph.offer_id, graph.marketplace_plan_id, graph.candidate_digest,
            graph.critical_content_digest, graph.targets, graph.common_plan_id,
            graph.common_run_id, graph.common_readback_digest, proof.budget_digest,
            graph.round1_digest, graph.round2_digest,
            _sha(_bytes(["synthetic-domain-final/v1", common_reservation_id, marketplace_plan_id])))
        return DomainFrozenCandidate(review, graph.display_bytes, graph.manifest_bytes,
                                     common_reservation_id, proof.evidence_kind)

    def inspect(self, db, common_reservation_id, marketplace_plan_id):
        """Read the registered domain graph in the caller's SQLite snapshot.

        This diagnostic has no nonce and never supplies an approvable
        descriptor. The caller must own the mapping from task to plan id.
        No schema initialization, GET write or connection opening occurs here.
        """
        result = {"schema_version": "local-operator-review/v1", "status": "BLOCKED",
                  "reservation_id": common_reservation_id,
                  "marketplace_plan_id": marketplace_plan_id,
                  "final_review_available": False, "execution_authority": False,
                  "external_writes_performed": [],
                  "blockers": ["COMMON_STANDING_POLICY_AUTHORITY_UNKNOWN",
                               "COMMON_HISTORICAL_WRITE_BUDGET_UNKNOWN",
                               "COMMON_OFFICIAL_PROVENANCE_NOT_ADMITTED"]}
        try:
            from shared_platform.r3_common_source_facts import NativeCommonSourceReader
            reader = self.authority if type(self.authority) is NativeCommonSourceReader else None
            graph = read_stored_domain_graph(db, marketplace_plan_id, native_reader=reader)
            result.update(offer_id=graph.offer_id, candidate_digest=graph.candidate_digest,
                          targets=list(graph.targets), display=json.loads(graph.display_bytes),
                          manifest=json.loads(graph.manifest_bytes),
                          evidence_kind="DOMAIN_GRAPH_READ_ONLY_COMMON_AUTHORITY_BLOCKED")
        except (DomainReviewBlocked, ValueError, TypeError, KeyError, AttributeError) as error:
            result["blockers"].insert(0, str(error))
        return result
