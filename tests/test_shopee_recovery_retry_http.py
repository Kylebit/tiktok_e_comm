import json
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

from modules.products import server as product_server
from shared_platform import product_publication_runner as runner_module
from shared_platform import publication_autopilot
from shared_platform import shopee_regional_recovery
from shared_platform import shopee_recovery_run_reconciliations
from shared_platform.product_publication_reports import (
    _safe_recovery_retry_authorization,
)
from modules.shopee import skill_regions


def _install_recovery_retry_boundary(tmp_path, monkeypatch, *, stored_receipt):
    labels = ("shopee:MY", "shopee:TH", "shopee:VN")
    snapshot_digest = "sha256:" + "a" * 64
    manifest_digest = "sha256:" + "4" * 64
    receipt_digest = "sha256:" + "5" * 64
    prepared = SimpleNamespace(
        offer_id="3956742887",
        revision=5,
        plan_id="plan-1",
        snapshot_digest=snapshot_digest,
        platform_scope=("SHOPEE",),
        target_labels_by_platform={"SHOPEE": labels},
        snapshot={"snapshot_digest": snapshot_digest},
    )
    reports_root = tmp_path / "reports" / "product-publication"
    preparation_root = reports_root.parent / "product-preparation"
    manifest_path = preparation_root / prepared.offer_id / "shopee-recovery-candidates" \
        / (manifest_digest.removeprefix("sha256:") + ".json")
    manifest_path.parent.mkdir(parents=True)
    manifest = {
        "manifest_digest": manifest_digest,
        "target_labels": list(labels),
        "prior_run_id": "product-center-shopee-old30c",
        "prior_report_digest": "sha256:" + "9" * 64,
        "targets": [
            {"target_label": label,
             "mutation_budget": {"shared_maximum": 0, "target_maximum": 3}}
            for label in labels
        ],
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    operations_root = tmp_path / "operations"
    operations_root.mkdir()

    class Reports:
        def __init__(self):
            self.reports_root = reports_root

    class Runs:
        path = tmp_path / "runs.db"

        def get_run_by_id(self, *, run_id):
            assert run_id == "product-center-shopee-recovery-retry-new"
            return {
                "request_identity": {
                    "kind": "SHOPEE_RECOVERY_RETRY",
                    "authority_digest": manifest_digest,
                    "reconciliation_receipt_digest": receipt_digest,
                    "retry_of_run_id": "product-center-shopee-failed1e6e",
                }
            }

    class ReceiptStore:
        def __init__(self, _path):
            pass

        def get(self, *, run_id, **validation):
            assert run_id == "product-center-shopee-failed1e6e"
            assert set(validation) == {
                "allowed_evidence_roots", "snapshot", "candidate", "approval"}
            return deepcopy(stored_receipt)

    candidate = {"candidate_digest": "6" * 64}
    approval = {"approval_digest": "7" * 64}
    monkeypatch.setenv("ORBIT_SHOPEE_RECOVERY_ENABLED", "1")
    monkeypatch.setenv("ORBIT_OPERATIONS_DATA_ROOT", str(operations_root))
    monkeypatch.setattr(runner_module, "prepare_product_publication_run", lambda **_: prepared)
    monkeypatch.setattr(product_server, "_release_store", lambda: object())
    monkeypatch.setattr(product_server, "_product_publication_report_store", lambda: Reports())
    monkeypatch.setattr(product_server, "_product_publication_run_store", lambda: Runs())
    monkeypatch.setattr(product_server, "_product_publication_execution_identity",
                        lambda _platform: {"skill_digest": "b" * 64,
                                           "git_commit": "c" * 40,
                                           "code_digest": "d" * 64})
    monkeypatch.setattr(publication_autopilot, "resolve_persisted_execution_authority",
                        lambda **_: (candidate, approval))
    monkeypatch.setattr(shopee_regional_recovery, "validate_recovery_manifest",
                        lambda value, **_: deepcopy(dict(value)))
    monkeypatch.setattr(shopee_regional_recovery, "build_recovery_executor",
                        lambda **_: (lambda _request: None))
    monkeypatch.setattr(skill_regions, "OfficialShopeeRegionRuntime", lambda: object())
    monkeypatch.setattr(shopee_recovery_run_reconciliations,
                        "ShopeeRecoveryRunReconciliationStore", ReceiptStore)
    request = {
        "offer_id": prepared.offer_id,
        "plan_id": prepared.plan_id,
        "snapshot_digest": prepared.snapshot_digest,
        "candidate_digest": candidate["candidate_digest"],
        "target_scope": list(labels),
        "recovery_manifest_digest": manifest_digest,
        "retry_of_run_id": "product-center-shopee-failed1e6e",
        "recovery_zero_write_receipt_digest": receipt_digest,
    }
    return prepared, manifest, request, receipt_digest


def test_recovery_retry_endpoint_binds_run_receipt_but_handover_uses_original_predecessor(
        tmp_path, monkeypatch):
    receipt = {
        "receipt_digest": "sha256:" + "5" * 64,
        "run_identity": {
            "run_id": "product-center-shopee-failed1e6e",
            "report_id": "publication-report:product-center-shopee-failed1e6e",
            "offer_id": "3956742887",
            "revision": 5,
            "plan_id": "plan-1",
            "snapshot_digest": "sha256:" + "a" * 64,
            "platform_scope": ["SHOPEE"],
            "target_count": 3,
            "execution_identity": {"skill_digest": "b" * 64,
                                   "git_commit": "c" * 40,
                                   "code_digest": "d" * 64},
            "request_identity": {"kind": "SHOPEE_RECOVERY",
                                 "authority_digest": "sha256:" + "4" * 64},
        },
    }
    prepared, manifest, request, _ = _install_recovery_retry_boundary(
        tmp_path, monkeypatch, stored_receipt=receipt)
    claim_calls = []
    executions = []

    def claim(**kwargs):
        claim_calls.append(kwargs)
        return SimpleNamespace(
            run_id="product-center-shopee-recovery-retry-new",
            report_id="publication-report:product-center-shopee-recovery-retry-new",
            created=len(claim_calls) == 1,
        )

    monkeypatch.setattr(runner_module, "claim_product_publication_request", claim)
    monkeypatch.setattr(product_server, "_execute_product_publication_background",
                        lambda **kwargs: executions.append(kwargs))
    monkeypatch.setattr(product_server, "_launch_product_publication_background",
                        lambda callback: callback())

    status, first = product_server._start_product_publication(request, platform="SHOPEE")
    assert status == 202 and first.get("reused") is not True
    assert len(executions) == 1
    assert claim_calls[0]["retry_of_run_id"] == request["retry_of_run_id"]
    assert claim_calls[0]["recovery_zero_write_receipt"] == receipt
    assert set(claim_calls[0]["recovery_zero_write_validation"]) == {
        "allowed_evidence_roots", "snapshot", "candidate", "approval"}
    assert executions[0]["retry_attempt"] == {
        "run_id": first["run_id"],
        "retry_of_run_id": manifest["prior_run_id"],
        "source_evidence_digest": manifest["prior_report_digest"],
    }
    assert executions[0]["recovery_authorization"]["manifest_digest"] \
        == manifest["manifest_digest"]
    retry_authority = executions[0]["recovery_retry_authorization"]
    assert retry_authority["retry_of_run_id"] == request["retry_of_run_id"]
    assert retry_authority["receipt_digest"] == receipt["receipt_digest"]
    assert retry_authority["failed_run_identity"] == receipt["run_identity"]

    status, second = product_server._start_product_publication(request, platform="SHOPEE")
    assert status == 202 and second["reused"] is True
    assert second["run_id"] == first["run_id"]
    assert len(executions) == 1


def test_recovery_retry_endpoint_rejects_missing_or_wrong_receipt_before_claim_and_launch(
        tmp_path, monkeypatch):
    prepared, manifest, request, receipt_digest = _install_recovery_retry_boundary(
        tmp_path, monkeypatch, stored_receipt=None)
    claims = []
    launches = []
    monkeypatch.setattr(runner_module, "claim_product_publication_request",
                        lambda **kwargs: claims.append(kwargs))
    monkeypatch.setattr(product_server, "_launch_product_publication_background",
                        lambda callback: launches.append(callback))

    status, body = product_server._start_product_publication(request, platform="SHOPEE")
    assert status == 409
    assert "receipt is unavailable or conflicts" in body["error"]
    assert claims == [] and launches == []

    missing_digest = dict(request)
    missing_digest.pop("recovery_zero_write_receipt_digest")
    status, body = product_server._start_product_publication(
        missing_digest, platform="SHOPEE")
    assert status == 409
    assert "requires a baseline-unchanged receipt" in body["error"]
    assert claims == [] and launches == []


def test_recovery_retry_report_authority_is_strict_and_tamper_evident():
    manifest_digest = "sha256:" + "4" * 64
    receipt_digest = "sha256:" + "5" * 64
    retry_of = "product-center-shopee-failed1e6e"
    failed = {
        "run_id": retry_of,
        "report_id": "publication-report:" + retry_of,
        "offer_id": "3956742887",
        "revision": 5,
        "plan_id": "plan-1",
        "snapshot_digest": "sha256:" + "a" * 64,
        "platform_scope": ["SHOPEE"],
        "target_count": 3,
        "execution_identity": {"skill_digest": "b" * 64,
                               "git_commit": "c" * 40,
                               "code_digest": "d" * 64},
        "request_identity": {"kind": "SHOPEE_RECOVERY",
                             "authority_digest": manifest_digest},
    }
    authority = {
        "schema_version": "shopee-recovery-retry-authorization/v1",
        "retry_of_run_id": retry_of,
        "receipt_digest": receipt_digest,
        "manifest_digest": manifest_digest,
        "failed_run_identity": failed,
        "successor_request_identity": {
            "kind": "SHOPEE_RECOVERY_RETRY",
            "authority_digest": manifest_digest,
            "reconciliation_receipt_digest": receipt_digest,
            "retry_of_run_id": retry_of,
        },
    }
    assert _safe_recovery_retry_authorization(authority) == authority
    for path, value in (
        (("retry_of_run_id",), "other-run"),
        (("receipt_digest",), "sha256:" + "6" * 64),
        (("manifest_digest",), "sha256:" + "7" * 64),
        (("failed_run_identity", "offer_id"), "not-an-offer"),
        (("successor_request_identity", "retry_of_run_id"), "other-run"),
    ):
        drift = deepcopy(authority)
        target = drift
        for name in path[:-1]:
            target = target[name]
        target[path[-1]] = value
        try:
            _safe_recovery_retry_authorization(drift)
        except ValueError:
            pass
        else:  # pragma: no cover - each lineage field must fail closed
            raise AssertionError(f"tampered authority passed: {path}")
