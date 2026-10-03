from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat

import pytest

from shared_platform.evidence_relocation_attestations import EvidenceRelocationResolver
from shared_platform.product_publication_reports import ProductPublicationReportStore
from shared_platform.product_publication_runs import ProductPublicationRunStore
from shared_platform.publication_autopilot import (
    load_final_approval_receipt,
    load_release_candidate,
)
from shared_platform.release_store import ReleaseStore
from shared_platform.shopee_known_zero_continuation import (
    ShopeeKnownZeroContinuationError,
    _strict_targets,
    validate_known_zero_continuation_manifest,
)
from shared_platform.shopee_reportless_recovery_reconciliations import (
    ShopeeReportlessRecoveryReconciliationStore,
    validate_reportless_continuation_manifest,
)


OFFLINE_FIXTURE_ENV = "ORBIT_OFFER395_KNOWN_ZERO_FIXTURE_ROOT"
KNOWN_ZERO_MANIFEST = "6dfad2346f1e491564e7fcc6b0695dbd981fe65af7e50d8d840ce38bc733842d.json"


def _reject_reparse_chain(path: Path) -> None:
    current = path
    while True:
        info = current.lstat()
        if current.is_symlink() or (
            getattr(info, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise ValueError("Offer395 offline fixture uses a symlink or reparse point")
        if current.parent == current:
            return
        current = current.parent


def _offline_fixture_paths(root: Path) -> dict[str, Path]:
    """Accept only an explicitly isolated copy, never the historical live roots."""
    if not root.is_absolute() or not root.is_dir() or root.is_symlink():
        raise ValueError("Offer395 offline fixture root must be an existing absolute directory")
    _reject_reparse_chain(root)
    root = root.resolve(strict=True)

    def local(relative: str, *, directory: bool = False) -> Path:
        path = root / relative
        if not path.exists() or path.is_dir() != directory:
            raise ValueError(f"Offer395 offline fixture is missing {relative}")
        _reject_reparse_chain(path)
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root):
            raise ValueError(f"Offer395 offline fixture escapes its root: {relative}")
        return resolved

    repo = local("repo", directory=True)
    ops = local("operations", directory=True)
    prep = local("repo/reports/product-preparation", directory=True)
    report_root = local("repo/reports/product-publication", directory=True)
    local("operations/sr-reportless/receipts", directory=True)
    return {
        "root": root,
        "repo": repo,
        "ops": ops,
        "prep": prep,
        "report_root": report_root,
        "run_db": local("repo/data/orbit_platform.db"),
        "frozen_db": local("frozen/orbit_platform.db"),
        "operations_db": local("operations/tasks.db"),
        "manifest": local(
            "repo/reports/product-preparation/3956742887/"
            f"shopee-recovery-candidates/{KNOWN_ZERO_MANIFEST}"
        ),
    }


def _local_captured_ref(ref: dict, root: Path) -> Path:
    """Verify a captured direct reference before any validator opens it."""
    path = Path(ref["path"])
    if not path.is_absolute() or not path.is_file() or path.is_symlink():
        raise ValueError("Offer395 captured reference is not a regular absolute file")
    _reject_reparse_chain(path)
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError("Offer395 captured reference points outside the offline fixture")
    digest = "sha256:" + hashlib.sha256(resolved.read_bytes()).hexdigest()
    if digest != ref.get("sha256"):
        raise ValueError("Offer395 captured reference digest changed")
    return resolved


def _local_relocation_files(attestation: dict, root: Path) -> None:
    target = Path(attestation["allowed_target_root"])
    if not target.is_absolute() or not target.resolve(strict=True).is_relative_to(root):
        raise ValueError("Offer395 relocated evidence root is outside the offline fixture")
    for entry in attestation["entries"]:
        _local_captured_ref(entry["relocated"], root)


def test_strict_target_parser_rejects_reordered_or_duplicate_scope():
    manifest = {
        "target_labels": ["shopee:MY", "shopee:TH"],
        "targets": [
            {"target_label": "shopee:MY"},
            {"target_label": "shopee:TH"},
        ],
    }
    assert _strict_targets(manifest)[0] == manifest["target_labels"]
    bad = deepcopy(manifest)
    bad["targets"].reverse()
    with pytest.raises(ShopeeKnownZeroContinuationError):
        _strict_targets(bad)
    bad = deepcopy(manifest)
    bad["target_labels"] = ["shopee:MY", "shopee:MY"]
    with pytest.raises(ShopeeKnownZeroContinuationError):
        _strict_targets(bad)


def test_offline_fixture_paths_and_references_fail_closed(tmp_path):
    root = tmp_path / "isolated"
    for relative in (
        "repo/data", "repo/reports/product-publication",
        "repo/reports/product-preparation/3956742887/shopee-recovery-candidates",
        "frozen", "operations/sr-reportless/receipts",
    ):
        (root / relative).mkdir(parents=True)
    for relative in (
        "repo/data/orbit_platform.db", "frozen/orbit_platform.db",
        "operations/tasks.db",
        "repo/reports/product-preparation/3956742887/"
        f"shopee-recovery-candidates/{KNOWN_ZERO_MANIFEST}",
    ):
        (root / relative).write_bytes(b"synthetic")
    paths = _offline_fixture_paths(root)
    assert paths["operations_db"] == (root / "operations/tasks.db").resolve()
    assert paths["manifest"] == (root / "repo/reports/product-preparation/3956742887/"
                                  "shopee-recovery-candidates" / KNOWN_ZERO_MANIFEST).resolve()

    outside = tmp_path / "outside.json"
    outside.write_bytes(b"outside")
    ref = {"path": str(outside), "sha256": "sha256:" + hashlib.sha256(b"outside").hexdigest()}
    with pytest.raises(ValueError, match="outside the offline fixture"):
        _local_captured_ref(ref, paths["root"])
    local = root / "reference.json"
    local.write_bytes(b"captured")
    ref = {"path": str(local), "sha256": "sha256:" + hashlib.sha256(b"captured").hexdigest()}
    assert _local_captured_ref(ref, paths["root"]) == local.resolve()
    local.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="digest changed"):
        _local_captured_ref(ref, paths["root"])
    with pytest.raises(ValueError, match="relocated evidence root"):
        _local_relocation_files({"allowed_target_root": str(tmp_path), "entries": []}, paths["root"])


def test_real_373_authorized_manifest_full_rebuild_and_tamper_rejection():
    configured = os.environ.get(OFFLINE_FIXTURE_ENV)
    if not configured:
        pytest.skip(f"set {OFFLINE_FIXTURE_ENV} to an isolated, rebased Offer395 fixture")
    fixture = _offline_fixture_paths(Path(configured))
    ops = fixture["ops"]
    frozen_db = fixture["frozen_db"]
    manifest_path = fixture["manifest"]
    prep = fixture["prep"]
    report_root = fixture["report_root"]
    roots = (report_root, prep, ops)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for ref in manifest["source_refs"].values():
        _local_captured_ref(ref, fixture["root"])
    d7_path = _local_captured_ref(manifest["source_refs"]["original_manifest"], fixture["root"])
    d7 = json.loads(d7_path.read_text(encoding="utf-8"))
    for ref in d7["source_refs"].values():
        _local_captured_ref(ref, fixture["root"])
    run_store = ProductPublicationRunStore(fixture["run_db"])
    report_store = ProductPublicationReportStore(
        fixture["run_db"], reports_root=report_root
    )
    release = ReleaseStore(frozen_db)
    snapshot = release.approved_publication_snapshot(
        offer_id="3956742887", snapshot_digest=d7["execution_snapshot_digest"]
    )
    candidate = load_release_candidate(
        "3956742887", d7["candidate_digest"], reports_root=prep
    )
    approval = load_final_approval_receipt(candidate, reports_root=prep)
    old_attestation_ref = d7["source_refs"]["evidence_relocation"]
    old_attestation_path = _local_captured_ref(old_attestation_ref, fixture["root"])
    old_attestation = json.loads(old_attestation_path.read_text(encoding="utf-8"))
    _local_relocation_files(old_attestation, fixture["root"])
    old_preflight = json.loads(_local_captured_ref(
        d7["source_refs"]["preflight"], fixture["root"]
    ).read_text(encoding="utf-8"))
    original = json.loads(_local_captured_ref(
        d7["source_refs"]["original_manifest"], fixture["root"]
    ).read_text(encoding="utf-8"))
    old_resolver = EvidenceRelocationResolver(
        old_attestation,
        allowed_target_root=old_attestation_path.parent,
        expected_authority_digests={
            "reportless_receipt": d7["source_receipt_digest"],
            "domain_closure": old_preflight["closure_digest"],
            "unauthorized_continuation": old_preflight[
                "unauthorized_continuation_digest"
            ],
            "continuation_preflight": d7["preflight_digest"],
            "original_recovery_manifest": original["manifest_digest"],
        },
    )
    old_store = ShopeeReportlessRecoveryReconciliationStore(
        ops / r"sr-reportless\receipts",
        run_store=run_store,
        report_store=report_store,
        snapshot=snapshot,
        candidate=candidate,
        approval=approval,
        allowed_evidence_roots=roots,
        evidence_resolver=old_resolver,
    )
    source_receipt = old_store.get(
        run_id=d7["direct_predecessor_run_id"],
        receipt_digest=d7["source_receipt_digest"],
    )

    def d7_validator(value):
        return validate_reportless_continuation_manifest(
            value,
            receipt=source_receipt,
            run_store=run_store,
            report_store=report_store,
            snapshot=snapshot,
            candidate=candidate,
            approval=approval,
            operations_db=fixture["operations_db"],
            allowed_evidence_roots=roots,
        )

    validation = dict(
        run_store=run_store,
        report_store=report_store,
        manifest_validator=d7_validator,
        operations_db=fixture["operations_db"],
        allowed_evidence_roots=roots,
    )
    checked = validate_known_zero_continuation_manifest(
        manifest,
        allowed_relocation_root=Path(
            manifest["source_refs"]["evidence_relocation"]["path"]
        ).parent,
        **validation,
    )
    assert checked == manifest
    assert checked["authorized"] is True
    assert checked["direct_predecessor_run_id"].endswith("373ea0658320f643b4634fb6d6ce671b")
    tampered = deepcopy(manifest)
    tampered["targets"][0]["item_id"] = "999"
    with pytest.raises(ShopeeKnownZeroContinuationError):
        validate_known_zero_continuation_manifest(
            tampered,
            allowed_relocation_root=Path(
                manifest["source_refs"]["evidence_relocation"]["path"]
            ).parent,
            **validation,
        )
