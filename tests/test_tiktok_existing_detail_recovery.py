import hashlib
import json
from copy import deepcopy

import pytest

from shared_platform.tiktok_existing_detail_recovery import (
    TikTokExistingDetailRecoveryLedger,
    TikTokRecoveryContractError,
    TikTokRecoveryStateError,
    build_recovery_claim,
    execute_registered_recovery,
)


def digest(value):
    return "sha256:" + hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def authority():
    rows = []
    for index, label in enumerate(("tiktok:HB_MY", "tiktok:LH_MY"), 1):
        new_url = f"https://images.example/{index}.png"
        rows.append(
            {
                "target_label": label,
                "detail_id": str(3300 + index),
                "shop_id": str(1600 + index),
                "detail_identity_digest": (str(index) * 64)[:64],
                "new_route_digest": "sha256:" + (str(index + 2) * 64)[:64],
                "new_position_7_url_sha256": hashlib.sha256(new_url.encode()).hexdigest(),
                "fresh_mutation_budget": 2,
                "ordered_operations": ["SAVE_DRAFT", "PUBLISH_TARGET"],
            }
        )
    contract = {
        "schema_version": "tiktok-existing-detail-route-recovery-runtime-contract/v1",
        "status": "REQUIRED_NOT_IMPLEMENTED",
        "offer_id": "3956742887",
        "seller_sku": "0988",
        "source_run_id": "product-center-tiktok-source",
        "source_report_id": "publication-report:product-center-tiktok-source",
        "ordered_target_labels": [row["target_label"] for row in rows],
        "targets": rows,
        "shared_mutation_budget": 0,
        "forbidden_operations": ["CREATE_DRAFT", "CLAIM_TO_SHOP"],
        "excluded_targets": ["tiktok:GB"],
        "claim_requirements": ["ATOMIC_DURABLE_RESERVATION_BEFORE_FIRST_WRITE"],
    }
    contract["contract_digest"] = digest(contract)
    candidate = {
        "schema_version": "tiktok-target-scoped-recovery-final-candidate/v1",
        "status": "READY_FOR_FINAL_REVIEW",
        "source_authority": {"source_run_id": contract["source_run_id"]},
        "recovery_snapshots": {
            "business_snapshot_digest": "sha256:" + "a" * 64,
            "execution_snapshot_digest": "sha256:" + "b" * 64,
        },
        "scope": {"ordered_target_labels": contract["ordered_target_labels"]},
        "targets": [
            {
                "target_label": row["target_label"],
                "existing_detail": {
                    "detail_id": row["detail_id"],
                    "shop_id": row["shop_id"],
                    "identity_digest": row["detail_identity_digest"],
                },
                "position_7": {
                    "new_url": f"https://images.example/{index}.png",
                    "new_url_sha256": row["new_position_7_url_sha256"],
                },
                "new_route_digest": row["new_route_digest"],
                "mutation_budget": {
                    "maximum": 2,
                    "ordered_operations": ["SAVE_DRAFT", "PUBLISH_TARGET"],
                    "forbidden_operations": ["CREATE_DRAFT", "CLAIM_TO_SHOP"],
                },
            }
            for index, row in enumerate(rows, 1)
        ],
    }
    candidate["candidate_digest"] = digest(candidate)
    approval = {
        "schema_version": "tiktok-target-scoped-recovery-final-approval/v1",
        "status": "APPROVED",
        "candidate_digest": candidate["candidate_digest"],
        "business_snapshot_digest": candidate["recovery_snapshots"]["business_snapshot_digest"],
        "execution_snapshot_digest": candidate["recovery_snapshots"]["execution_snapshot_digest"],
        "source_run_id": contract["source_run_id"],
        "ordered_target_labels": contract["ordered_target_labels"],
    }
    approval["approval_digest"] = digest(approval)
    return contract, candidate, approval


def test_claim_binds_exact_source_detail_route_candidate_and_approval():
    contract, candidate, approval = authority()
    claim = build_recovery_claim(contract=contract, candidate=candidate, approval=approval)
    assert claim["source_run_id"] == "product-center-tiktok-source"
    assert claim["candidate_digest"] == candidate["candidate_digest"]
    assert claim["approval_digest"] == approval["approval_digest"]
    assert [row["detail_id"] for row in claim["targets"]] == ["3301", "3302"]
    assert claim["shared_mutation_budget"] == 0
    assert claim["forbidden_operations"] == ["CREATE_DRAFT", "CLAIM_TO_SHOP"]


@pytest.mark.parametrize("drift", ["source", "detail", "route", "scope", "approval"])
def test_any_authority_drift_fails_closed(drift):
    contract, candidate, approval = authority()
    if drift == "source":
        candidate["source_authority"]["source_run_id"] = "other"
    elif drift == "detail":
        candidate["targets"][0]["existing_detail"]["detail_id"] = "999"
    elif drift == "route":
        candidate["targets"][0]["new_route_digest"] = "sha256:" + "f" * 64
    elif drift == "scope":
        candidate["scope"]["ordered_target_labels"].reverse()
    else:
        approval["ordered_target_labels"].reverse()
        approval["approval_digest"] = digest({k: v for k, v in approval.items() if k != "approval_digest"})
    if drift != "approval":
        candidate["candidate_digest"] = digest({k: v for k, v in candidate.items() if k != "candidate_digest"})
    with pytest.raises(TikTokRecoveryContractError):
        build_recovery_claim(contract=contract, candidate=candidate, approval=approval)


def test_offline_end_to_end_save_then_publish_with_readback(tmp_path):
    contract, candidate, approval = authority()
    claim = build_recovery_claim(contract=contract, candidate=candidate, approval=approval)
    ledger = TikTokExistingDetailRecoveryLedger(tmp_path / "ledger.db")
    ledger.register_claim(claim)

    save = ledger.reserve(claim_digest=claim["claim_digest"], target_label="tiktok:HB_MY", operation="SAVE_DRAFT")
    ledger.record_result(reservation_digest=save["reservation_digest"], outcome="CONFIRMED")
    publish = ledger.reserve(claim_digest=claim["claim_digest"], target_label="tiktok:HB_MY", operation="PUBLISH_TARGET")
    with pytest.raises(TikTokRecoveryStateError, match="provider readback"):
        ledger.record_result(reservation_digest=publish["reservation_digest"], outcome="CONFIRMED")
    ledger.record_result(reservation_digest=publish["reservation_digest"], outcome="CONFIRMED", provider_readback_verified=True)

    target = ledger.snapshot(claim_digest=claim["claim_digest"])["targets"][0]
    assert target["state"] == "SUCCEEDED"
    assert target["attempts"] == 2
    assert [event["operation"] for event in target["events"]] == ["SAVE_DRAFT", "PUBLISH_TARGET"]
    with pytest.raises(TikTokRecoveryStateError, match="exhausted"):
        ledger.reserve(claim_digest=claim["claim_digest"], target_label="tiktok:HB_MY", operation="PUBLISH_TARGET")


def test_unknown_consumes_reservation_and_forces_reconciliation(tmp_path):
    contract, candidate, approval = authority()
    claim = build_recovery_claim(contract=contract, candidate=candidate, approval=approval)
    ledger = TikTokExistingDetailRecoveryLedger(tmp_path / "ledger.db")
    ledger.register_claim(claim)
    reservation = ledger.reserve(claim_digest=claim["claim_digest"], target_label="tiktok:LH_MY", operation="SAVE_DRAFT")
    ledger.record_result(reservation_digest=reservation["reservation_digest"], outcome="UNKNOWN")

    target = ledger.snapshot(claim_digest=claim["claim_digest"])["targets"][1]
    assert target["state"] == "RECONCILIATION_REQUIRED"
    assert target["attempts"] == 1
    with pytest.raises(TikTokRecoveryStateError, match="reconciliation"):
        ledger.reserve(claim_digest=claim["claim_digest"], target_label="tiktok:LH_MY", operation="PUBLISH_TARGET")


@pytest.mark.parametrize("operation", ["CREATE_DRAFT", "CLAIM_TO_SHOP", "PUBLISH_TARGET"])
def test_forbidden_or_out_of_order_operations_never_reserve(tmp_path, operation):
    contract, candidate, approval = authority()
    claim = build_recovery_claim(contract=contract, candidate=candidate, approval=approval)
    ledger = TikTokExistingDetailRecoveryLedger(tmp_path / "ledger.db")
    ledger.register_claim(claim)
    with pytest.raises(TikTokRecoveryStateError):
        ledger.reserve(claim_digest=claim["claim_digest"], target_label="tiktok:HB_MY", operation=operation)
    assert ledger.snapshot(claim_digest=claim["claim_digest"])["targets"][0]["attempts"] == 0


def test_claim_registration_is_idempotent_but_source_cannot_fork(tmp_path):
    contract, candidate, approval = authority()
    claim = build_recovery_claim(contract=contract, candidate=candidate, approval=approval)
    ledger = TikTokExistingDetailRecoveryLedger(tmp_path / "ledger.db")
    ledger.register_claim(claim)
    ledger.register_claim(claim)
    other = deepcopy(claim)
    other["candidate_digest"] = "sha256:" + "f" * 64
    other.pop("claim_digest")
    other["claim_digest"] = digest(other)
    with pytest.raises(TikTokRecoveryStateError, match="source run"):
        ledger.register_claim(other)


def test_controlled_transport_entry_is_offline_end_to_end(tmp_path):
    contract, candidate, approval = authority()
    claim = build_recovery_claim(contract=contract, candidate=candidate, approval=approval)
    ledger = TikTokExistingDetailRecoveryLedger(tmp_path / "ledger.db")
    ledger.register_claim(claim)
    calls = []

    class FakeTransport:
        def verify_existing_detail(self, target):
            calls.append(("VERIFY", target["target_label"]))
            return {key: target[key] for key in ("detail_id", "shop_id", "detail_identity_digest")}

        def save_existing_detail(self, target):
            calls.append(("SAVE_DRAFT", target["target_label"]))
            return {"confirmed": True}

        def publish_existing_detail(self, target):
            calls.append(("PUBLISH_TARGET", target["target_label"]))
            return {"confirmed": True, "provider_readback_verified": True}

    result = execute_registered_recovery(
        ledger=ledger,
        claim=claim,
        source_run_id=contract["source_run_id"],
        ordered_target_labels=contract["ordered_target_labels"],
        transport=FakeTransport(),
    )
    assert [row["outcome"] for row in result["targets"]] == ["SUCCEEDED", "SUCCEEDED"]
    assert calls == [
        ("VERIFY", "tiktok:HB_MY"),
        ("SAVE_DRAFT", "tiktok:HB_MY"),
        ("PUBLISH_TARGET", "tiktok:HB_MY"),
        ("VERIFY", "tiktok:LH_MY"),
        ("SAVE_DRAFT", "tiktok:LH_MY"),
        ("PUBLISH_TARGET", "tiktok:LH_MY"),
    ]


def test_controlled_transport_unknown_stops_target_without_retry(tmp_path):
    contract, candidate, approval = authority()
    claim = build_recovery_claim(contract=contract, candidate=candidate, approval=approval)
    ledger = TikTokExistingDetailRecoveryLedger(tmp_path / "ledger.db")
    ledger.register_claim(claim)

    class UnknownTransport:
        def verify_existing_detail(self, target):
            return {key: target[key] for key in ("detail_id", "shop_id", "detail_identity_digest")}
        def save_existing_detail(self, target):
            raise TimeoutError("provider response lost")
        def publish_existing_detail(self, target):
            raise AssertionError("must never publish after unknown save")

    result = execute_registered_recovery(
        ledger=ledger,
        claim=claim,
        source_run_id=contract["source_run_id"],
        ordered_target_labels=contract["ordered_target_labels"],
        transport=UnknownTransport(),
    )
    assert [row["outcome"] for row in result["targets"]] == [
        "RECONCILIATION_REQUIRED",
        "RECONCILIATION_REQUIRED",
    ]
    assert all(row["attempts"] == 1 for row in result["ledger"]["targets"])


@pytest.mark.parametrize(
    "drift",
    ("detail", "shop", "url", "candidate", "approval", "source_run"),
)
def test_execution_rejects_mutable_claim_body_before_transport_or_reservation(
    tmp_path, drift
):
    contract, candidate, approval = authority()
    claim = build_recovery_claim(
        contract=contract, candidate=candidate, approval=approval
    )
    ledger = TikTokExistingDetailRecoveryLedger(tmp_path / "ledger.db")
    ledger.register_claim(claim)
    changed = deepcopy(claim)
    if drift == "detail":
        changed["targets"][0]["detail_id"] = "9999"
    elif drift == "shop":
        changed["targets"][0]["shop_id"] = "9999"
    elif drift == "url":
        changed["targets"][0]["new_position_7_url"] = "https://images.example/wrong.png"
    elif drift == "candidate":
        changed["candidate_digest"] = "sha256:" + "c" * 64
    elif drift == "approval":
        changed["approval_digest"] = "sha256:" + "d" * 64
    else:
        changed["source_run_id"] = "different-source-run"

    class NoTransport:
        def verify_existing_detail(self, target):
            raise AssertionError("transport must not be called")
        def save_existing_detail(self, target):
            raise AssertionError("transport must not be called")
        def publish_existing_detail(self, target):
            raise AssertionError("transport must not be called")

    with pytest.raises(TikTokRecoveryContractError, match="claim"):
        execute_registered_recovery(
            ledger=ledger,
            claim=changed,
            source_run_id=contract["source_run_id"],
            ordered_target_labels=contract["ordered_target_labels"],
            transport=NoTransport(),
        )
    assert all(
        row["attempts"] == 0
        for row in ledger.snapshot(claim_digest=claim["claim_digest"])["targets"]
    )
