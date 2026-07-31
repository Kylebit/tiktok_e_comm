from __future__ import annotations

import json
import threading

import pytest

from modules.miaoshou.client import MiaoshouBusinessRejectedError
from modules.miaoshou.collectbox_claim import (
    ACCEPTED,
    ALREADY_PRESENT,
    FAILED,
    CLAIM_PATH,
    CollectBoxClaimRequest,
    CollectBoxPlatformClaimRequest,
    MiaoshouAlreadyPresentObservation,
    claim_common_collectbox,
    claim_common_collectbox_platform,
)


def _request() -> CollectBoxClaimRequest:
    return CollectBoxClaimRequest(
        common_detail_id="3846511157",
        platforms=("tiktok", "shopee"),
        idempotency_key="release-plan:abc:collectbox-claim",
    )


def _success(platform: str, detail_id: int) -> dict[str, object]:
    return {
        "result": "success",
        "code": "200",
        "data": {
            "platformCollectBoxDetailIdMap": {
                platform: {"3846511157": detail_id}
            }
        },
    }


def test_claim_uses_only_exact_endpoint_and_one_platform_per_serial_call() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def post(path: str, body: dict[str, object]) -> dict[str, object]:
        calls.append((path, body))
        platform = body["detailSerialNumberPlatformList"][0]["platform"]  # type: ignore[index]
        return _success(str(platform), 71001 if platform == "tiktok" else 72001)

    receipt = claim_common_collectbox(_request(), post=post)

    assert calls == [
        (
            CLAIM_PATH,
            {
                "detailSerialNumberPlatformList": [
                    {
                        "detailId": 3846511157,
                        "platform": "tiktok",
                        "serialNumber": 1,
                    }
                ]
            },
        ),
        (
            CLAIM_PATH,
            {
                "detailSerialNumberPlatformList": [
                    {
                        "detailId": 3846511157,
                        "platform": "shopee",
                        "serialNumber": 1,
                    }
                ]
            },
        ),
    ]
    assert [row.status for row in receipt.platform_results] == [
        ACCEPTED,
        ACCEPTED,
    ]
    assert [row.platform_detail_id for row in receipt.platform_results] == [
        71001,
        72001,
    ]
    assert [row.write_class for row in receipt.platform_results] == [
        "miaoshou:collectbox:claim:tiktok",
        "miaoshou:collectbox:claim:shopee",
    ]


def test_single_platform_seam_returns_internal_identity_and_redacted_receipt() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def post(path: str, body: dict[str, object]) -> dict[str, object]:
        calls.append((path, body))
        return _success("shopee", 72001)

    receipt = claim_common_collectbox_platform(
        CollectBoxPlatformClaimRequest(
            common_detail_id=3846511157,
            platform="shopee",
            idempotency_key="job:1:target:shopee",
        ),
        post=post,
    )

    assert len(calls) == 1
    assert receipt.result.status == ACCEPTED
    assert receipt.result.platform_detail_id == 72001
    assert receipt.result.write_class == "miaoshou:collectbox:claim:shopee"
    public = json.dumps(receipt.public_projection(), sort_keys=True)
    assert "3846511157" not in public
    assert "72001" not in public
    assert "job:1:target:shopee" not in public
    assert receipt.result.platform_detail_identity_digest in public


def test_rate_limit_retries_only_current_platform_once_after_injected_wait() -> None:
    calls: list[str] = []
    waits: list[float] = []
    shopee_calls = 0

    def post(path: str, body: dict[str, object]) -> dict[str, object]:
        nonlocal shopee_calls
        assert path == CLAIM_PATH
        platform = str(body["detailSerialNumberPlatformList"][0]["platform"])  # type: ignore[index]
        calls.append(platform)
        if platform == "tiktok":
            return _success(platform, 71001)
        shopee_calls += 1
        if shopee_calls == 1:
            raise MiaoshouBusinessRejectedError(
                "rate limited", code="accountApiQpsRateLimit"
            )
        return _success(platform, 72001)

    receipt = claim_common_collectbox(_request(), post=post, wait=waits.append)

    assert calls == ["tiktok", "shopee", "shopee"]
    assert waits == [3.0]
    assert [row.attempt_count for row in receipt.platform_results] == [1, 2]
    assert [row.status for row in receipt.platform_results] == [
        ACCEPTED,
        ACCEPTED,
    ]


def test_exact_rate_limit_is_bounded_to_one_retry() -> None:
    calls = 0
    waits: list[float] = []

    def post(_path: str, _body: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        raise MiaoshouBusinessRejectedError(
            "rate limited", code="accountApiQpsRateLimit"
        )

    receipt = claim_common_collectbox_platform(
        CollectBoxPlatformClaimRequest(
            common_detail_id=3846511157,
            platform="tiktok",
            idempotency_key="bounded-retry",
        ),
        post=post,
        wait=waits.append,
    )

    assert calls == 2
    assert waits == [3.0]
    assert receipt.result.status == FAILED
    assert receipt.result.attempt_count == 2
    assert receipt.result.retry_safe is True
    assert receipt.result.reconciliation_required is False


def test_single_platform_invocations_are_account_serialized() -> None:
    first_entered = threading.Event()
    second_started = threading.Event()
    release_first = threading.Event()
    calls: list[str] = []
    receipts: list[object] = []

    def post(_path: str, body: dict[str, object]) -> dict[str, object]:
        platform = str(body["detailSerialNumberPlatformList"][0]["platform"])  # type: ignore[index]
        calls.append(platform)
        if platform == "tiktok":
            first_entered.set()
            assert release_first.wait(timeout=2)
            return _success(platform, 71001)
        return _success(platform, 72001)

    def invoke(platform: str) -> None:
        if platform == "shopee":
            second_started.set()
        receipts.append(
            claim_common_collectbox_platform(
                CollectBoxPlatformClaimRequest(
                    common_detail_id=3846511157,
                    platform=platform,
                    idempotency_key=f"serialized-{platform}",
                ),
                post=post,
            )
        )

    first = threading.Thread(target=invoke, args=("tiktok",), daemon=True)
    second = threading.Thread(target=invoke, args=("shopee",), daemon=True)
    first.start()
    assert first_entered.wait(timeout=2)
    second.start()
    assert second_started.wait(timeout=2)
    assert calls == ["tiktok"]
    release_first.set()
    first.join(timeout=2)
    second.join(timeout=2)
    assert not first.is_alive()
    assert not second.is_alive()
    assert calls == ["tiktok", "shopee"]
    assert len(receipts) == 2


def test_non_exact_rate_limit_code_is_not_retried() -> None:
    calls = 0
    waits: list[float] = []

    def post(_path: str, _body: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        raise MiaoshouBusinessRejectedError(
            "different documented spelling", code="accountQpsRateLimit"
        )

    receipt = claim_common_collectbox(
        CollectBoxClaimRequest(
            common_detail_id=123,
            platforms=("tiktok", "shopee"),
            idempotency_key="idempotency-1",
        ),
        post=post,
        wait=waits.append,
    )

    assert calls == 2
    assert waits == []
    assert all(row.status == FAILED for row in receipt.platform_results)
    assert all(row.attempt_count == 1 for row in receipt.platform_results)
    assert all(row.outcome_unknown is False for row in receipt.platform_results)


def test_bare_already_present_without_exact_identity_requires_reconciliation() -> None:
    calls: list[str] = []

    def post(_path: str, body: dict[str, object]) -> dict[str, object]:
        platform = str(body["detailSerialNumberPlatformList"][0]["platform"])  # type: ignore[index]
        calls.append(platform)
        if platform == "tiktok":
            raise MiaoshouBusinessRejectedError(
                "already present", code="alreadyClaimed"
            )
        return _success(platform, 72001)

    receipt = claim_common_collectbox(_request(), post=post)

    assert calls == ["tiktok", "shopee"]
    assert receipt.platform_results[0].status == FAILED
    assert receipt.platform_results[0].platform_detail_id is None
    assert receipt.platform_results[0].outcome_unknown is False
    assert receipt.platform_results[0].retry_safe is False
    assert receipt.platform_results[0].reconciliation_required is True
    assert receipt.platform_results[1].status == ACCEPTED


def test_already_present_requires_exact_observed_platform_detail_identity() -> None:
    def post(_path: str, body: dict[str, object]) -> dict[str, object]:
        platform = str(body["detailSerialNumberPlatformList"][0]["platform"])  # type: ignore[index]
        if platform == "tiktok":
            raise MiaoshouAlreadyPresentObservation(71001)
        return _success(platform, 72001)

    receipt = claim_common_collectbox(_request(), post=post)

    first = receipt.platform_results[0]
    assert first.status == ALREADY_PRESENT
    assert first.platform_detail_id == 71001
    assert first.platform_detail_identity_digest
    assert first.retry_safe is False
    assert first.reconciliation_required is False


def test_business_rejection_is_failed_known_and_does_not_retry() -> None:
    calls: list[str] = []

    def post(_path: str, body: dict[str, object]) -> dict[str, object]:
        platform = str(body["detailSerialNumberPlatformList"][0]["platform"])  # type: ignore[index]
        calls.append(platform)
        if platform == "tiktok":
            raise MiaoshouBusinessRejectedError(
                "product missing", code="productNotFound"
            )
        return _success(platform, 72001)

    receipt = claim_common_collectbox(_request(), post=post)

    assert calls == ["tiktok", "shopee"]
    first = receipt.platform_results[0]
    assert first.status == FAILED
    assert first.reason_code == "productNotFound"
    assert first.attempt_count == 1
    assert first.outcome_unknown is False
    assert first.retry_safe is True
    assert first.reconciliation_required is False


def test_unknown_transport_is_not_retried_and_preserves_unknown_write() -> None:
    calls: list[str] = []
    waits: list[float] = []

    def post(_path: str, body: dict[str, object]) -> dict[str, object]:
        platform = str(body["detailSerialNumberPlatformList"][0]["platform"])  # type: ignore[index]
        calls.append(platform)
        if platform == "tiktok":
            raise TimeoutError("connection lost after request bytes")
        return _success(platform, 72001)

    receipt = claim_common_collectbox(_request(), post=post, wait=waits.append)

    assert calls == ["tiktok", "shopee"]
    assert waits == []
    first = receipt.platform_results[0]
    assert first.status == FAILED
    assert first.reason_code == "transport_outcome_unknown"
    assert first.attempt_count == 1
    assert first.outcome_unknown is True
    assert first.write_outcome == "UNKNOWN"
    assert first.retry_safe is False
    assert first.reconciliation_required is True


def test_malformed_success_is_unknown_after_invocation_and_not_retried() -> None:
    calls = 0

    def post(_path: str, _body: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return {"result": "success", "data": {"platformCollectBoxDetailIdMap": {}}}

    receipt = claim_common_collectbox(_request(), post=post)

    assert calls == 2
    assert all(row.status == FAILED for row in receipt.platform_results)
    assert all(row.reason_code == "response_identity_unavailable" for row in receipt.platform_results)
    assert all(row.outcome_unknown is True for row in receipt.platform_results)


def test_receipt_and_public_projection_are_deterministic_and_redacted() -> None:
    def post(_path: str, body: dict[str, object]) -> dict[str, object]:
        platform = str(body["detailSerialNumberPlatformList"][0]["platform"])  # type: ignore[index]
        return _success(platform, 71001 if platform == "tiktok" else 72001)

    first = claim_common_collectbox(_request(), post=post)
    second = claim_common_collectbox(_request(), post=post)
    assert first == second
    assert first.receipt_digest == second.receipt_digest

    public = first.public_projection()
    encoded = json.dumps(public, sort_keys=True)
    assert public["status_counts"] == {
        ACCEPTED: 2,
        ALREADY_PRESENT: 0,
        FAILED: 0,
    }
    assert public["receipt_digest"] == first.receipt_digest
    assert "3846511157" not in encoded
    assert "71001" not in encoded
    assert "72001" not in encoded
    assert "release-plan:abc:collectbox-claim" not in encoded
    assert "detailSerialNumberPlatformList" not in encoded


@pytest.mark.parametrize(
    ("common_detail_id", "platforms", "idempotency_key"),
    [
        (True, ("tiktok", "shopee"), "key"),
        (0, ("tiktok", "shopee"), "key"),
        ("01", ("tiktok", "shopee"), "key"),
        (123, ["tiktok", "shopee"], "key"),
        (123, ("shopee", "tiktok"), "key"),
        (123, ("tiktok",), "key"),
        (123, ("tiktok", "shopee"), ""),
        (123, ("tiktok", "shopee"), True),
    ],
)
def test_request_contract_fails_closed(
    common_detail_id: object,
    platforms: object,
    idempotency_key: object,
) -> None:
    with pytest.raises((TypeError, ValueError)):
        CollectBoxClaimRequest(
            common_detail_id=common_detail_id,  # type: ignore[arg-type]
            platforms=platforms,  # type: ignore[arg-type]
            idempotency_key=idempotency_key,  # type: ignore[arg-type]
        )
