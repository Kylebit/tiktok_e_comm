from __future__ import annotations

import pytest

from shared_platform.publication_write_budget import (
    PublicationWriteBudgetExceeded,
    PublicationWriteBudgetLedger,
)


def _ledger(*, shared: int = 1, per_target: int = 2) -> PublicationWriteBudgetLedger:
    return PublicationWriteBudgetLedger(
        platform="TIKTOK",
        target_labels=("tiktok:PH", "tiktok:MY"),
        budget={
            "shared_maximum": shared,
            "per_target_maximum": per_target,
        },
    )


def test_shared_mutation_is_reserved_before_provider_call_and_cannot_exceed_budget():
    ledger = _ledger(shared=1)

    reservation = ledger.reserve_shared("create_shared_draft")

    assert reservation == {
        "sequence": 1,
        "scope": "shared",
        "target_label": None,
        "operation": "create_shared_draft",
        "attempt_count": 1,
    }
    assert ledger.shared_attempt_count == 1
    with pytest.raises(PublicationWriteBudgetExceeded, match="shared mutation attempt budget"):
        ledger.reserve_shared("retry_shared_draft")


def test_target_mutations_are_bounded_independently_and_unknown_outcome_stays_reserved():
    ledger = _ledger(per_target=1)

    ledger.reserve_target("tiktok:PH", "publish_target")

    assert ledger.target_attempt_counts == {"tiktok:PH": 1, "tiktok:MY": 0}
    with pytest.raises(PublicationWriteBudgetExceeded, match="tiktok:PH"):
        ledger.reserve_target("tiktok:PH", "retry_after_timeout")
    ledger.reserve_target("tiktok:MY", "publish_target")
    assert ledger.total_attempt_count == 2


def test_invalid_target_and_unsafe_operation_fail_before_reservation():
    ledger = _ledger()

    with pytest.raises(ValueError, match="approved target"):
        ledger.reserve_target("tiktok:TH", "publish_target")
    with pytest.raises(ValueError, match="operation"):
        ledger.reserve_shared("provider request with spaces")
    assert ledger.total_attempt_count == 0


def test_confirmed_writes_cannot_exceed_reserved_mutation_attempts():
    ledger = _ledger(shared=3)
    ledger.reserve_shared("upload_images")
    ledger.reserve_shared("create_item")

    ledger.assert_confirmed_writes_within_attempts(1)
    ledger.assert_confirmed_writes_within_attempts(2)
    with pytest.raises(PublicationWriteBudgetExceeded, match="confirmed writes"):
        ledger.assert_confirmed_writes_within_attempts(3)


def test_snapshot_preserves_limits_attempts_and_reservation_order():
    ledger = _ledger(shared=2, per_target=1)
    ledger.reserve_shared("create_global_item")
    ledger.reserve_target("tiktok:PH", "publish_target")

    assert ledger.snapshot() == {
        "schema_version": "publication-mutation-budget/v1",
        "platform": "TIKTOK",
        "limits": {"shared_maximum": 2, "per_target_maximum": 1},
        "attempts": {
            "shared": 1,
            "per_target": {"tiktok:PH": 1, "tiktok:MY": 0},
            "total": 2,
        },
        "reservations": [
            {"sequence": 1, "scope": "shared", "target_label": None, "operation": "create_global_item", "attempt_count": 1},
            {"sequence": 2, "scope": "target", "target_label": "tiktok:PH", "operation": "publish_target", "attempt_count": 1},
        ],
    }
