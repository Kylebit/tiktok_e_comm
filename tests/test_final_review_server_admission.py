"""Synthetic policy tests. No browser, Windows SID, provider, or real store."""

import threading
import time

import pytest

from shared_platform.final_review_server_admission import (
    ApprovalBlocked,
    DurableAtomicReceiptSource,
    FinalApprovalAdmission,
    FrozenReview,
    TrustedReceipt,
    compare_before_execution,
    reject_http_approval,
)


def frozen(**changes):
    values = dict(
        offer_id="3956742887", plan_id="plan-a", candidate_digest="sha256:candidate-a",
        critical_content_digest="sha256:critical-a", targets=("tiktok:MY", "shopee:MY"),
        common_plan_id="common-a", common_run_id="run-a",
        common_readback_digest="sha256:readback-a",
        common_budget_digest="sha256:budget-a", round1_digest="sha256:r1-a",
        round2_digest="sha256:r2-a", action_id="action-a",
    )
    values.update(changes)
    return FrozenReview(**values)


@pytest.fixture(autouse=True)
def fixed_production_clock(monkeypatch):
    monkeypatch.setattr("shared_platform.final_review_server_admission.time.time", lambda: 110)


class SyntheticAtomicSource(DurableAtomicReceiptSource):
    """Only a test double for a future OS-authenticated, server-owned writer."""

    def __init__(self, receipt):
        self.receipt = receipt
        self.used = False
        self.lock = threading.Lock()

    def read_pending(self, receipt_id):
        with self.lock:
            if self.used or self.receipt.receipt_id != receipt_id:
                return None
            return self.receipt

    def consume_exact(self, receipt_id, binding_digest):
        with self.lock:
            if self.used or self.receipt.receipt_id != receipt_id:
                return None
            if self.receipt.binding.digest() != binding_digest:
                return None
            now_epoch = int(time.time())
            if not self.receipt.issued_at_epoch <= now_epoch < self.receipt.expires_at_epoch:
                return None
            self.used = True
            return self.receipt


def receipt(binding=None, **changes):
    values = dict(receipt_id="decision-a", binding=binding or frozen(),
                  operator_subject="synthetic-test-subject", issued_at_epoch=100,
                  expires_at_epoch=130, decision="approve")
    values.update(changes)
    return TrustedReceipt(**values)


def blocked(code, fn):
    with pytest.raises(ApprovalBlocked) as caught:
        fn()
    assert caught.value.code == code


@pytest.mark.parametrize("payload", [
    {"user_approved": True, "approved_by": "Kyle"},
    {"user_approved": True, "approved_by": "Kyle", "receipt_id": "decision-a"},
    {"user_approved": True, "approved_by": "Kyle", "x_windows_sid": "S-1-forged"},
    {},
])
def test_legacy_http_claims_never_become_decision(payload):
    blocked("TRUSTED_CHANNEL_REQUIRED", lambda: reject_http_approval(payload))


def test_no_installed_receipt_source_is_default_closed():
    blocked("TRUSTED_CHANNEL_UNAVAILABLE",
            lambda: FinalApprovalAdmission().accept_receipt("decision-a", frozen()))


def test_request_cannot_supply_historical_clock_to_revive_expired_receipt(monkeypatch):
    monkeypatch.setattr("shared_platform.final_review_server_admission.time.time", lambda: 200)
    gate = FinalApprovalAdmission(SyntheticAtomicSource(receipt()))
    with pytest.raises(TypeError):
        gate.accept_receipt("decision-a", frozen(), now_epoch=110)
    blocked("RECEIPT_EXPIRED", lambda: gate.accept_receipt("decision-a", frozen()))


def test_read_pending_cannot_extend_receipt_past_actual_expiry(monkeypatch):
    clock = [110]
    monkeypatch.setattr("shared_platform.final_review_server_admission.time.time", lambda: clock[0])

    class DelayedReadSource(SyntheticAtomicSource):
        def read_pending(self, receipt_id):
            pending = super().read_pending(receipt_id)
            clock[0] = 131  # The source was blocked until after the receipt expired.
            return pending

    source = DelayedReadSource(receipt())
    blocked("RECEIPT_EXPIRED", lambda: FinalApprovalAdmission(source).accept_receipt(
        "decision-a", frozen()))
    assert not source.used


def test_source_must_check_ttl_at_atomic_consume_boundary(monkeypatch):
    clock = [110]
    monkeypatch.setattr("shared_platform.final_review_server_admission.time.time", lambda: clock[0])

    class DelayedCommitSource(SyntheticAtomicSource):
        def consume_exact(self, receipt_id, binding_digest):
            clock[0] = 131  # Receipt expires after policy check, before CAS.
            return super().consume_exact(receipt_id, binding_digest)

    source = DelayedCommitSource(receipt())
    blocked("RECEIPT_UNAVAILABLE_OR_USED", lambda: FinalApprovalAdmission(source).accept_receipt(
        "decision-a", frozen()))
    assert not source.used


def test_faulty_source_returning_post_expiry_receipt_still_grants_no_approval(monkeypatch):
    clock = [110]
    monkeypatch.setattr("shared_platform.final_review_server_admission.time.time", lambda: clock[0])

    class FaultySource(SyntheticAtomicSource):
        def consume_exact(self, receipt_id, binding_digest):
            result = super().consume_exact(receipt_id, binding_digest)
            clock[0] = 131
            return result

    source = FaultySource(receipt())
    blocked("RECEIPT_EXPIRED_AFTER_CONSUMPTION",
            lambda: FinalApprovalAdmission(source).accept_receipt("decision-a", frozen()))
    assert source.used  # Still needs a durable recovery plan; never business approval.


def test_source_without_atomic_durable_contract_is_rejected():
    class ReadOnlySource:
        def read_pending(self, receipt_id):
            return receipt()
    blocked("TRUSTED_RECEIPT_SOURCE_INVALID", lambda: FinalApprovalAdmission(ReadOnlySource()))


@pytest.mark.parametrize("change", [
    {"offer_id": "other"}, {"candidate_digest": "sha256:other"},
    {"targets": ("tiktok:MY",)}, {"targets": ("shopee:MY", "tiktok:MY")},
    {"common_plan_id": "other"}, {"common_run_id": "other"},
    {"common_readback_digest": "sha256:other"},
    {"common_budget_digest": "sha256:other"},
    {"critical_content_digest": "sha256:other"},
    {"round1_digest": "sha256:other"}, {"round2_digest": "sha256:other"},
    {"action_id": "other"},
])
def test_exact_receipt_cannot_cross_offer_candidate_target_or_common(change):
    source = SyntheticAtomicSource(receipt())
    blocked("FROZEN_REVIEW_CHANGED", lambda: FinalApprovalAdmission(source).accept_receipt(
        "decision-a", frozen(**change)))
    assert not source.used


def test_exact_receipt_is_one_use_even_under_concurrency():
    source = SyntheticAtomicSource(receipt())
    gate = FinalApprovalAdmission(source)
    outcomes = []

    def attempt():
        try:
            outcomes.append(gate.accept_receipt("decision-a", frozen()))
        except ApprovalBlocked as error:
            outcomes.append(error.code)

    threads = [threading.Thread(target=attempt) for _ in range(8)]
    for item in threads:
        item.start()
    for item in threads:
        item.join()
    assert sum(isinstance(item, TrustedReceipt) for item in outcomes) == 1
    assert outcomes.count("RECEIPT_UNAVAILABLE_OR_USED") == 7


def test_expired_or_wrong_decision_rejected_before_source_consumption(monkeypatch):
    monkeypatch.setattr("shared_platform.final_review_server_admission.time.time", lambda: 130)
    expired_source = SyntheticAtomicSource(receipt())
    blocked("RECEIPT_EXPIRED", lambda: FinalApprovalAdmission(expired_source).accept_receipt(
        "decision-a", frozen()))
    assert not expired_source.used
    monkeypatch.setattr("shared_platform.final_review_server_admission.time.time", lambda: 110)
    rejected_source = SyntheticAtomicSource(receipt(decision="reject"))
    blocked("APPROVAL_DECISION_REQUIRED", lambda: FinalApprovalAdmission(rejected_source).accept_receipt(
        "decision-a", frozen()))
    assert not rejected_source.used


def test_execution_recheck_distinguishes_material_change_from_rebinding_and_wait():
    approved = frozen()
    cases = [
        (frozen(), (), "MATCH_ONLY"),
        (frozen(candidate_digest="sha256:new"), (), "NONCRITICAL_REBIND_REQUIRED"),
        (frozen(common_run_id="run-b"), (), "NONCRITICAL_REBIND_REQUIRED"),
        (frozen(critical_content_digest="sha256:changed"), (), "NEW_FINAL_REVIEW_REQUIRED"),
        (frozen(targets=("tiktok:MY",)), (), "NEW_FINAL_REVIEW_REQUIRED"),
        (frozen(common_readback_digest="sha256:changed"), (), "NEW_FINAL_REVIEW_REQUIRED"),
        (frozen(), ("inventory_stale",), "EXECUTION_BLOCKED_APPROVAL_PRESERVED"),
    ]
    for current, blockers, disposition in cases:
        result = compare_before_execution(approved, current, execution_blockers=blockers)
        assert result.disposition == disposition
        assert result.authorization is False


@pytest.mark.parametrize("targets", [(), ("tiktok:MY", "tiktok:MY"), ("",)])
def test_invalid_or_ambiguous_target_scope_is_rejected(targets):
    blocked("FROZEN_REVIEW_INVALID", lambda: frozen(targets=targets))
