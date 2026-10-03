from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

import pytest

from shared_platform.product_publication_runner import (
    PreparedPublicationRun,
    claim_product_publication_request,
)
from shared_platform.product_publication_runs import ProductPublicationRunStore


PREDECESSOR = "product-center-shopee-4bc697d923e8cbb569e8533a3c63e932"
OLD_RUNS = {
    PREDECESSOR,
    "product-center-shopee-1e6e675a650ad03208a6f8d8996408a6",
    "product-center-shopee-30c4f4c7c06a92aadfbf15a3b5389531",
}


class _Reports:
    def get_report_by_run(self, *, run_id):
        return None


class _Release:
    def approved_publication_snapshot(self, **_kwargs):  # pragma: no cover - guard
        raise AssertionError("continuation predecessor must not reload historical authority")


def _fixture(tmp_path, monkeypatch):
    run_store = ProductPublicationRunStore(tmp_path / "runs.db")
    execution_identity = {
        "skill_digest": "1" * 64,
        "git_commit": "2" * 40,
        "code_digest": "3" * 64,
    }
    prepared = PreparedPublicationRun(
        offer_id="3956742887", revision=5,
        plan_id="omnichannel:" + "a" * 64,
        snapshot_digest="sha256:" + "b" * 64,
        platform_scope=("SHOPEE",),
        target_labels_by_platform={"SHOPEE": (
            "shopee:MY", "shopee:TH", "shopee:VN")},
        snapshot={},
    )
    predecessor = run_store.create_run(
        run_id=PREDECESSOR, offer_id=prepared.offer_id,
        revision=prepared.revision, plan_id=prepared.plan_id,
        snapshot_digest=prepared.snapshot_digest, platform_scope=("SHOPEE",),
        target_count=3, execution_identity=execution_identity,
        request_identity={
            "kind": "SHOPEE_RECOVERY_RETRY",
            "authority_digest": "sha256:" + "8" * 64,
            "reconciliation_receipt_digest": "sha256:" + "9" * 64,
            "retry_of_run_id": "product-center-shopee-1e6e675a650ad03208a6f8d8996408a6",
        },
    )
    run_store.mark_failed(
        run_id=predecessor.run_id, failure_code="RUNNER_INFRASTRUCTURE_FAILED")
    source_receipt_digest = "sha256:" + "c" * 64
    preflight_digest = "sha256:" + "d" * 64
    manifest_digest = "sha256:" + "e" * 64
    continuation = {
        "schema_version": "shopee-recovery-continuation/v1",
        "receipt_digest": preflight_digest,
        "target_scope": ["shopee:MY", "shopee:TH", "shopee:VN"],
        "exact_remaining_differences": [],
        "action_budgets": {
            label: ["update_copy_and_description_media", "list_existing_item"]
            for label in ("shopee:MY", "shopee:TH", "shopee:VN")
        },
    }
    authority = {
        "schema_version": "shopee-reportless-recovery-execution-manifest/v1",
        "status": "READY_ZERO_WRITE_PREFLIGHT",
        "authorized": True,
        "manifest_digest": manifest_digest,
        "offer_id": prepared.offer_id,
        "product_revision": prepared.revision,
        "plan_id": prepared.plan_id,
        "execution_snapshot_digest": prepared.snapshot_digest,
        "direct_predecessor_run_id": PREDECESSOR,
        "source_receipt_digest": source_receipt_digest,
        "preflight_digest": preflight_digest,
        "evidence_relocation_digest": "sha256:" + "7" * 64,
        "target_labels": list(prepared.target_labels_by_platform["SHOPEE"]),
        "continuation": deepcopy(continuation),
    }
    receipt = {
        "schema_version": "shopee-reportless-recovery-reconciliation/v1",
        "result": "REMAINING_DIFF",
        "attempt_closed": True,
        "mutation_lock": False,
        "receipt_digest": source_receipt_digest,
        "attempt": {
            "run_id": PREDECESSOR,
            "report_id": predecessor.report_id,
            "offer_id": prepared.offer_id,
            "revision": prepared.revision,
            "plan_id": prepared.plan_id,
            "snapshot_digest": prepared.snapshot_digest,
            "platform_scope": ["SHOPEE"],
            "target_count": 3,
        },
        "authority_identity": {
            "offer_id": prepared.offer_id,
            "plan_id": prepared.plan_id,
            "snapshot_digest": prepared.snapshot_digest,
            "target_labels": list(prepared.target_labels_by_platform["SHOPEE"]),
        },
    }
    arguments = dict(
        prepared=prepared, platform="SHOPEE", execution_identity=execution_identity,
        run_store=run_store, report_store=_Reports(), release_store=_Release(),
        recovery_manifest_digest=manifest_digest,
        recovery_continuation=continuation,
        reportless_continuation_authority=authority,
        reportless_continuation_validation={
            "candidate": {}, "approval": {}, "operations_db": tmp_path / "tasks.db",
            "allowed_evidence_roots": [tmp_path],
        },
        recovery_reconciliation_receipt=receipt,
        recovery_evidence_roots=[tmp_path],
    )
    def _deep_validator(value, **kwargs):
        assert kwargs["receipt"] == receipt
        assert kwargs["run_store"] is run_store
        assert kwargs["operations_db"] == tmp_path / "tasks.db"
        return deepcopy(dict(value))

    monkeypatch.setattr(
        "shared_platform.shopee_reportless_recovery_reconciliations."
        "validate_reportless_continuation_manifest",
        _deep_validator,
    )
    return run_store, authority, receipt, arguments


def test_reportless_continuation_claim_is_new_and_same_identity_replays(tmp_path, monkeypatch):
    run_store, authority, _, arguments = _fixture(tmp_path, monkeypatch)
    first = claim_product_publication_request(**arguments)
    second = claim_product_publication_request(**arguments)

    assert first.created is True and second.created is False
    assert first.run_id == second.run_id and first.run_id not in OLD_RUNS
    assert run_store.get_run_by_id(run_id=first.run_id)["request_identity"] == {
        "kind": "SHOPEE_RECOVERY_CONTINUATION",
        "authority_digest": authority["manifest_digest"],
        "predecessor_run_id": PREDECESSOR,
        "source_receipt_digest": authority["source_receipt_digest"],
        "preflight_digest": authority["preflight_digest"],
        "evidence_relocation_digest": authority["evidence_relocation_digest"],
    }


@pytest.mark.parametrize("field", [
    "manifest_digest", "direct_predecessor_run_id",
    "source_receipt_digest", "preflight_digest",
])
def test_reportless_continuation_claim_rejects_lineage_drift(tmp_path, monkeypatch, field):
    _, authority, receipt, arguments = _fixture(tmp_path, monkeypatch)
    changed = deepcopy(authority)
    if field == "direct_predecessor_run_id":
        changed[field] = "product-center-shopee-other"
    else:
        changed[field] = "sha256:" + "f" * 64
    arguments["reportless_continuation_authority"] = changed
    with pytest.raises(ValueError, match="continuation"):
        claim_product_publication_request(**arguments)


def test_reportless_continuation_claim_fails_closed_when_deep_validation_fails(
        tmp_path, monkeypatch):
    _, _, _, arguments = _fixture(tmp_path, monkeypatch)

    def _reject(*_args, **_kwargs):
        raise ValueError("immutable evidence drift")

    monkeypatch.setattr(
        "shared_platform.shopee_reportless_recovery_reconciliations."
        "validate_reportless_continuation_manifest",
        _reject,
    )
    with pytest.raises(ValueError, match="deep validation failed"):
        claim_product_publication_request(**arguments)


REAL_REPO = Path(__file__).parents[1]
REAL_OPS = Path(r"D:\OrbitHive\runtime\operations-stable-1c5f5260")
REAL_MANIFEST = REAL_REPO / r"reports\product-preparation\3956742887\shopee-recovery-candidates\d7c993cb34165105d56eeae287529309bc12a6945cdafec8f4f2a9272a22f173.json"
REAL_ATTESTATION = REAL_OPS / r"sr-reportless\evidence-relocations\offer395-4bc-590e\attestation-f03a70707eea007458eb3db9e01801e6c9ce3ccd044290b11e41a7cb6b4d465a.json"
REAL_RELEASE_DB = Path(r"D:\OrbitHive\candidate-20260908\frozen-shopee-master-price\data\orbit_platform.db")


@pytest.mark.skipif(not all(path.is_file() for path in (
    REAL_MANIFEST, REAL_ATTESTATION, REAL_RELEASE_DB, REAL_OPS / "tasks.db",
    REAL_REPO / "data" / "orbit_platform.db",
)), reason="exact reportless continuation authority is unavailable")
def test_real_attested_continuation_claim_uses_stable_roots_only(tmp_path):
    from shared_platform.evidence_relocation_attestations import (
        EvidenceRelocationResolver, OFFER_395_REQUIRED_AUTHORITY_DIGESTS,
    )
    from shared_platform.product_publication_reports import ProductPublicationReportStore
    from shared_platform.publication_autopilot import resolve_persisted_execution_authority
    from shared_platform.release_store import ReleaseStore
    from shared_platform.shopee_reportless_recovery_reconciliations import (
        ShopeeReportlessRecoveryReconciliationStore,
    )

    manifest = json.loads(REAL_MANIFEST.read_text(encoding="utf-8"))
    release = ReleaseStore(REAL_RELEASE_DB)
    from shared_platform.product_publication_runner import prepare_product_publication_run
    prepared = prepare_product_publication_run(
        release_store=release, offer_id=manifest["offer_id"],
        snapshot_digest=manifest["execution_snapshot_digest"],
        platform_scope=("SHOPEE",), target_scope=tuple(manifest["target_labels"]),
    )
    candidate, approval = resolve_persisted_execution_authority(
        snapshot=prepared.snapshot, platform_scope=("SHOPEE",),
        target_labels=prepared.target_labels_by_platform["SHOPEE"],
        reports_root=REAL_REPO / "reports" / "product-preparation",
        candidate_digest=manifest["candidate_digest"],
    )
    copied_db = tmp_path / "runs.db"
    shutil.copy2(REAL_REPO / "data" / "orbit_platform.db", copied_db)
    # The stable ledger now includes the real successor for this authority.
    # Remove only that successor from the isolated copy so this fixture keeps
    # exercising first claim followed by an idempotent replay.
    with sqlite3.connect(copied_db) as connection:
        rows = connection.execute(
            "SELECT run_id, request_identity_json FROM product_publication_runs"
        ).fetchall()
        claimed = [
            run_id for run_id, raw_identity in rows
            if raw_identity is not None
            and json.loads(raw_identity).get("kind")
            == "SHOPEE_RECOVERY_CONTINUATION"
            and json.loads(raw_identity).get("authority_digest")
            == manifest["manifest_digest"]
        ]
        for run_id in claimed:
            connection.execute(
                "DELETE FROM product_publication_run_events WHERE run_id = ?",
                (run_id,),
            )
            connection.execute(
                "DELETE FROM product_publication_runs WHERE run_id = ?", (run_id,)
            )
        connection.commit()
    run_store = ProductPublicationRunStore(copied_db)
    report_store = ProductPublicationReportStore(
        copied_db, reports_root=REAL_REPO / "reports" / "product-publication")
    relocation = json.loads(REAL_ATTESTATION.read_text(encoding="utf-8"))
    resolver = EvidenceRelocationResolver(
        relocation, allowed_target_root=REAL_ATTESTATION.parent,
        expected_authority_digests=OFFER_395_REQUIRED_AUTHORITY_DIGESTS,
    )
    roots = (report_store.reports_root,
             REAL_REPO / "reports" / "product-preparation", REAL_OPS)
    receipt = ShopeeReportlessRecoveryReconciliationStore(
        REAL_OPS / "sr-reportless" / "receipts", run_store=run_store,
        report_store=report_store, snapshot=prepared.snapshot, candidate=candidate,
        approval=approval, allowed_evidence_roots=roots,
        evidence_resolver=resolver,
    ).get(run_id=PREDECESSOR, receipt_digest=manifest["source_receipt_digest"])
    assert receipt is not None
    execution_identity = {
        "skill_digest": "1" * 64, "git_commit": "2" * 40,
        "code_digest": "3" * 64,
    }
    arguments = dict(
        prepared=prepared, platform="SHOPEE", execution_identity=execution_identity,
        run_store=run_store, report_store=report_store, release_store=release,
        recovery_manifest_digest=manifest["manifest_digest"],
        recovery_continuation=manifest["continuation"],
        reportless_continuation_authority=manifest,
        reportless_continuation_validation={
            "candidate": candidate, "approval": approval,
            "operations_db": REAL_OPS / "tasks.db",
            "allowed_evidence_roots": roots,
        },
        recovery_reconciliation_receipt=receipt,
        recovery_evidence_roots=roots,
    )
    first = claim_product_publication_request(**arguments)
    second = claim_product_publication_request(**arguments)
    assert first.created is True and second.created is False
    assert first.run_id == second.run_id and first.run_id not in OLD_RUNS
    stored = run_store.get_run_by_id(run_id=first.run_id)
    assert stored["request_identity"]["evidence_relocation_digest"] == \
        manifest["evidence_relocation_digest"]

    from shared_platform import operations_domain_guard
    from shared_platform.workbench_engine import WorkbenchEngine
    tasks_copy = tmp_path / "tasks.db"
    shutil.copy2(REAL_OPS / "tasks.db", tasks_copy)
    # The stable operations ledger also contains the completed real successor.
    # Remove that exact operation only in this copy so the fixture can replay
    # the original reportless handoff without colliding with later history.
    with sqlite3.connect(tasks_copy) as connection:
        completed_ref = (
            "shopee-known-zero-recovery:"
            "049e21bfc0d1336a67aaa5e03616519dfad1cbc97fe8fc48a77484e9f5950308"
        )
        operation_ids = [
            row[0] for row in connection.execute(
                "SELECT operation_id FROM workbench_domain_operations "
                "WHERE readback_ref = ?", (completed_ref,)
            )
        ]
        for existing_operation_id in operation_ids:
            connection.execute(
                "DELETE FROM workbench_domain_locks WHERE operation_id = ?",
                (existing_operation_id,),
            )
            connection.execute(
                "DELETE FROM workbench_domain_operations WHERE operation_id = ?",
                (existing_operation_id,),
            )
        connection.commit()
    engine = WorkbenchEngine(tasks_copy, {"code_version": "reportless-copy-test"})
    original_engine_for = operations_domain_guard.engine_for
    operations_domain_guard.engine_for = lambda _root: engine
    try:
        domain_arguments = dict(
            retry_attempt={
                "run_id": first.run_id,
                "retry_of_run_id": manifest["direct_predecessor_run_id"],
                "source_evidence_digest": manifest["preflight_digest"],
            },
            target_scope=manifest["target_labels"],
            recovery_continuation=manifest["continuation"],
            recovery_authorization=manifest,
            expected_run_id=first.run_id,
        )
        _, operation_id, first_domain = operations_domain_guard.begin_snapshot_publication(
            release, manifest["offer_id"], manifest["execution_snapshot_digest"],
            "SHOPEE", tmp_path, **domain_arguments,
        )
        with pytest.raises(ValueError, match="requires reconciliation"):
            operations_domain_guard.begin_snapshot_publication(
                release, manifest["offer_id"], manifest["execution_snapshot_digest"],
                "SHOPEE", tmp_path, **domain_arguments,
            )
    finally:
        operations_domain_guard.engine_for = original_engine_for
    with sqlite3.connect(tasks_copy) as connection:
        locks = connection.execute(
            "SELECT resource FROM workbench_domain_locks WHERE operation_id=? ORDER BY resource",
            (operation_id,),
        ).fetchall()
    assert len(locks) == 3
    assert first_domain["acquired"] is True
    assert {row[0] for row in locks} == {
        'product:["shopee:MY","0988"]',
        'product:["shopee:TH","0988"]',
        'product:["shopee:VN","0988"]',
    }
