from __future__ import annotations

import json
from pathlib import Path
import shutil
import sqlite3

import pytest

from shared_platform.product_publication_runner import (
    claim_product_publication_request,
    prepare_product_publication_run,
)
from shared_platform.product_publication_runs import ProductPublicationRunStore


REPO = Path(__file__).parents[1]
OPS = Path(r"D:\OrbitHive\runtime\operations-stable-1c5f5260")
RELEASE_DB = Path(
    r"D:\OrbitHive\candidate-20260908\frozen-shopee-master-price\data\orbit_platform.db"
)
MANIFEST = REPO / (
    "reports/product-preparation/3956742887/shopee-recovery-candidates/"
    "6dfad2346f1e491564e7fcc6b0695dbd981fe65af7e50d8d840ce38bc733842d.json"
)
KNOWN_ZERO_RECEIPT = OPS / (
    "sr-known-zero/receipts/"
    "c2a678f6c35fe55cffaff99e005ddf2655cc60ac8c5bc91abf320109e61aec63/"
    "receipt.json"
)
SOURCE_REPORTLESS_RECEIPT = OPS / (
    "sr-reportless/receipts/"
    "05d20ddb59d861a95c520d8ae9174fad1bb7af18ffa4720e98a38c02a224ea87/"
    "receipt.json"
)
RELOCATION_ROOT = OPS / r"sr-known-zero\evidence-relocations\373fresh"
OLD_RUNS = {
    "product-center-shopee-373ea0658320f643b4634fb6d6ce671b",
    "product-center-shopee-4bc697d923e8cbb569e8533a3c63e932",
    "product-center-shopee-1e6e675a650ad03208a6f8d8996408a6",
    "product-center-shopee-30c4f4c7c06a92aadfbf15a3b5389531",
}


@pytest.mark.skipif(
    not all(path.is_file() for path in (
        MANIFEST,
        KNOWN_ZERO_RECEIPT,
        SOURCE_REPORTLESS_RECEIPT,
        RELEASE_DB,
        OPS / "tasks.db",
        REPO / "data" / "orbit_platform.db",
    )),
    reason="exact known-zero continuation authority is unavailable",
)
def test_real_known_zero_claim_deep_validates_and_is_exactly_idempotent(tmp_path):
    from shared_platform.product_publication_reports import (
        ProductPublicationReportStore,
    )
    from shared_platform.publication_autopilot import (
        resolve_persisted_execution_authority,
    )
    from shared_platform.release_store import ReleaseStore

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    known_zero_receipt = json.loads(KNOWN_ZERO_RECEIPT.read_text(encoding="utf-8"))
    source_receipt = json.loads(
        SOURCE_REPORTLESS_RECEIPT.read_text(encoding="utf-8")
    )
    release = ReleaseStore(RELEASE_DB)
    prepared = prepare_product_publication_run(
        release_store=release,
        offer_id=manifest["offer_id"],
        snapshot_digest=manifest["execution_snapshot_digest"],
        platform_scope=("SHOPEE",),
        target_scope=tuple(manifest["target_labels"]),
    )
    candidate, approval = resolve_persisted_execution_authority(
        snapshot=prepared.snapshot,
        platform_scope=("SHOPEE",),
        target_labels=prepared.target_labels_by_platform["SHOPEE"],
        reports_root=REPO / "reports" / "product-preparation",
        candidate_digest=manifest["candidate_digest"],
    )
    copied_db = tmp_path / "runs.db"
    shutil.copy2(REPO / "data" / "orbit_platform.db", copied_db)
    run_store = ProductPublicationRunStore(copied_db)
    report_store = ProductPublicationReportStore(
        copied_db,
        reports_root=REPO / "reports" / "product-publication",
    )
    roots = (
        report_store.reports_root,
        REPO / "reports" / "product-preparation",
        OPS,
    )
    arguments = dict(
        prepared=prepared,
        platform="SHOPEE",
        execution_identity={
            "skill_digest": "1" * 64,
            "git_commit": "2" * 40,
            "code_digest": "3" * 64,
        },
        run_store=run_store,
        report_store=report_store,
        release_store=release,
        recovery_manifest_digest=manifest["manifest_digest"],
        recovery_continuation=manifest["continuation"],
        known_zero_continuation_authority=manifest,
        known_zero_continuation_validation={
            "candidate": candidate,
            "approval": approval,
            "operations_db": OPS / "tasks.db",
            "allowed_evidence_roots": roots,
            "allowed_relocation_root": RELOCATION_ROOT,
            "source_reportless_receipt": source_receipt,
        },
        known_zero_receipt=known_zero_receipt,
    )

    first = claim_product_publication_request(**arguments)
    second = claim_product_publication_request(**arguments)

    assert first.created is True
    assert second.created is False
    assert first.run_id == second.run_id
    assert first.run_id not in OLD_RUNS
    stored = run_store.get_run_by_id(run_id=first.run_id)
    assert stored["state"] == "QUEUED"
    assert stored["request_identity"] == {
        "kind": "SHOPEE_KNOWN_ZERO_CONTINUATION",
        "authority_digest": manifest["manifest_digest"],
        "predecessor_run_id": manifest["direct_predecessor_run_id"],
        "source_receipt_digest": manifest["source_receipt_digest"],
        "source_execution_manifest_digest": manifest[
            "source_execution_manifest_digest"
        ],
        "preflight_digest": manifest["preflight_digest"],
        "evidence_relocation_digest": manifest["evidence_relocation_digest"],
    }

    from shared_platform import operations_domain_guard
    from shared_platform.workbench_engine import WorkbenchEngine

    tasks_copy = tmp_path / "tasks.db"
    shutil.copy2(OPS / "tasks.db", tasks_copy)
    engine = WorkbenchEngine(tasks_copy, {"code_version": "known-zero-copy-test"})
    original_engine_for = operations_domain_guard.engine_for
    operations_domain_guard.engine_for = lambda _root: engine
    try:
        retry_attempt = {
            "run_id": first.run_id,
            "retry_of_run_id": manifest["direct_predecessor_run_id"],
            "source_evidence_digest": manifest["preflight_digest"],
        }
        _, operation_id, domain = operations_domain_guard.begin_snapshot_publication(
            release,
            manifest["offer_id"],
            manifest["execution_snapshot_digest"],
            "SHOPEE",
            tmp_path,
            retry_attempt=retry_attempt,
            target_scope=manifest["target_labels"],
            recovery_continuation=manifest["continuation"],
            recovery_authorization=manifest,
            expected_run_id=first.run_id,
        )
    finally:
        operations_domain_guard.engine_for = original_engine_for

    with sqlite3.connect(tasks_copy) as connection:
        operation = connection.execute(
            "SELECT state, resources_json FROM workbench_domain_operations WHERE operation_id=?",
            (operation_id,),
        ).fetchone()
        locks = connection.execute(
            "SELECT resource FROM workbench_domain_locks WHERE operation_id=? ORDER BY resource",
            (operation_id,),
        ).fetchall()
        predecessor = connection.execute(
            "SELECT state FROM workbench_domain_operations "
            "WHERE readback_ref LIKE 'shopee-known-zero-recovery:%'"
        ).fetchone()
    assert domain["acquired"] is True
    assert operation is not None and operation[0] == "inflight"
    assert set(json.loads(operation[1])) == {
        'product:["shopee:MY","0988"]',
        'product:["shopee:TH","0988"]',
        'product:["shopee:VN","0988"]',
    }
    assert {row[0] for row in locks} == {
        'product:["shopee:MY","0988"]',
        'product:["shopee:TH","0988"]',
        'product:["shopee:VN","0988"]',
    }
    assert predecessor is not None and predecessor[0] == "completed"


def test_known_zero_request_identity_rejects_missing_source_manifest(tmp_path):
    store = ProductPublicationRunStore(tmp_path / "runs.db")
    request_identity = {
        "kind": "SHOPEE_KNOWN_ZERO_CONTINUATION",
        "authority_digest": "sha256:" + "a" * 64,
        "predecessor_run_id": "product-center-shopee-predecessor",
        "source_receipt_digest": "sha256:" + "b" * 64,
        "preflight_digest": "sha256:" + "c" * 64,
        "evidence_relocation_digest": "sha256:" + "d" * 64,
    }
    with pytest.raises(ValueError, match="request_identity"):
        store.create_run(
            run_id="product-center-shopee-known-zero-test",
            offer_id="3956742887",
            revision=5,
            plan_id="omnichannel:" + "e" * 64,
            snapshot_digest="sha256:" + "f" * 64,
            platform_scope=("SHOPEE",),
            target_count=3,
            execution_identity={
                "skill_digest": "1" * 64,
                "git_commit": "2" * 40,
                "code_digest": "3" * 64,
            },
            request_identity=request_identity,
        )


def test_known_zero_request_identity_deduplicates_only_exact_evidence(tmp_path):
    store = ProductPublicationRunStore(tmp_path / "runs.db")
    predecessor = "product-center-shopee-predecessor"
    request_identity = {
        "kind": "SHOPEE_KNOWN_ZERO_CONTINUATION",
        "authority_digest": "sha256:" + "a" * 64,
        "predecessor_run_id": predecessor,
        "source_receipt_digest": "sha256:" + "b" * 64,
        "source_execution_manifest_digest": "sha256:" + "c" * 64,
        "preflight_digest": "sha256:" + "d" * 64,
        "evidence_relocation_digest": "sha256:" + "e" * 64,
    }
    common = dict(
        offer_id="3956742887",
        revision=5,
        plan_id="omnichannel:" + "f" * 64,
        snapshot_digest="sha256:" + "0" * 64,
        platform_scope=("SHOPEE",),
        target_count=3,
        execution_identity={
            "skill_digest": "1" * 64,
            "git_commit": "2" * 40,
            "code_digest": "3" * 64,
        },
        retry_of_run_id=predecessor,
    )
    first = store.create_run(
        run_id="product-center-shopee-known-zero-one",
        request_identity=request_identity,
        **common,
    )
    same = store.create_run(
        run_id="product-center-shopee-known-zero-one",
        request_identity=request_identity,
        **common,
    )
    changed = dict(request_identity)
    changed["evidence_relocation_digest"] = "sha256:" + "9" * 64
    second = store.create_run(
        run_id="product-center-shopee-known-zero-two",
        request_identity=changed,
        **common,
    )

    assert first.created is True and same.created is False
    assert second.created is True and second.run_id != first.run_id
