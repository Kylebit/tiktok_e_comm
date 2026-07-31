from __future__ import annotations

import hashlib
import json
import sys
import types
from dataclasses import dataclass

import pytest

from modules.miaoshou.client import MiaoshouBusinessRejectedError
from modules.miaoshou.collectbox_claim import (
    MiaoshouAlreadyPresentObservation,
)


def _canonical_digest(value: object) -> str:
    payload = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@pytest.fixture()
def contract(monkeypatch: pytest.MonkeyPatch) -> types.ModuleType:
    module = types.ModuleType("shared_platform.collectbox_action")
    module.SUCCEEDED = "SUCCEEDED"
    module.FAILED_RETRYABLE = "FAILED_RETRYABLE"
    module.RECONCILIATION_REQUIRED = "RECONCILIATION_REQUIRED"
    module.IMPORTED = "IMPORTED"
    module.ALREADY_PRESENT = "ALREADY_PRESENT"

    def common_collectbox_identity_digest(plan_id: str, detail_id: object) -> str:
        return _canonical_digest(
            {
                "schema_version": "common-collectbox-identity/v1",
                "plan_id": plan_id,
                "common_collect_box_detail_id": str(detail_id),
            }
        )

    @dataclass(frozen=True)
    class CollectBoxPlatformRequest:
        action_id: str
        plan_id: str
        platform: str
        common_collect_box_detail_id: str
        common_collectbox_identity_digest: str
        payload_digest: str
        targets_digest: str
        idempotency_key: str
        approved_plan_payload: object
        approved_targets: tuple[str, ...]

    @dataclass(frozen=True)
    class CollectBoxPlatformResult:
        status: str
        outcome: str | None = None
        platform_detail_id: str | None = None
        external_writes: tuple[str, ...] = ()
        external_write_count: int | None = 0
        receipt_evidence: object = None
        error_category: str | None = None
        error_code: str | None = None
        error_detail: str | None = None

    module.common_collectbox_identity_digest = common_collectbox_identity_digest
    module.CollectBoxPlatformRequest = CollectBoxPlatformRequest
    module.CollectBoxPlatformResult = CollectBoxPlatformResult
    monkeypatch.setitem(sys.modules, module.__name__, module)
    from domains.channel_operations import collectbox_action_adapters

    def prepare_fixture(**kwargs):
        written = bool(kwargs["initial_claim_written"])
        platform = kwargs["platform"]
        return {
            "primary_platform_detail_id": kwargs["initial_platform_detail_id"],
            "target_count": 1,
            "platform_detail_count": 1,
            "external_writes": (
                (f"miaoshou:collectbox:claim:{platform}",) if written else ()
            ),
            "external_write_count": 1 if written else 0,
            "checks": {
                "approved_targets_exact": True,
                "approved_prices_exact": True,
                "approved_content_exact": True,
                "readback_exact": True,
                "publish_not_invoked": True,
            },
        }

    monkeypatch.setattr(
        collectbox_action_adapters,
        "prepare_selected_platform_collectbox",
        prepare_fixture,
    )
    return module


def _request(contract: types.ModuleType, *, platform: str = "TIKTOK"):
    plan_id = "plan-097"
    detail_id = "3846511157"
    return contract.CollectBoxPlatformRequest(
        action_id="collectbox-action-1",
        plan_id=plan_id,
        platform=platform,
        common_collect_box_detail_id=detail_id,
        common_collectbox_identity_digest=(
            contract.common_collectbox_identity_digest(plan_id, detail_id)
        ),
        payload_digest="a" * 64,
        targets_digest="b" * 64,
        idempotency_key="c" * 64,
        approved_plan_payload={"product_revision": 31},
        approved_targets=("tiktok:MX", "shopee:MY"),
    )


def _success(platform: str, detail_id: int) -> dict[str, object]:
    return {
        "result": "success",
        "data": {
            "platformCollectBoxDetailIdMap": {
                platform: {"3846511157": detail_id}
            }
        },
    }


def test_accepted_roundtrip_maps_to_imported_with_exact_write(
    contract: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def post(path: str, body: dict[str, object]) -> dict[str, object]:
        calls.append((path, body))
        return _success("tiktok", 71001)

    monkeypatch.setattr("modules.miaoshou.client.post_open", post)
    from domains.channel_operations.collectbox_action_adapters import (
        execute_collectbox_platform,
    )

    result = execute_collectbox_platform(_request(contract))

    assert result.status == "SUCCEEDED"
    assert result.outcome == "IMPORTED"
    assert result.platform_detail_id == "71001"
    assert result.external_writes == ("miaoshou:collectbox:claim:tiktok",)
    assert result.external_write_count == 1
    assert len(calls) == 1
    public = json.dumps(result.receipt_evidence, sort_keys=True)
    assert "3846511157" not in public
    assert "71001" not in public
    assert all(
        "platform_detail_id" not in node
        for node in (
            result.receipt_evidence,
            result.receipt_evidence["result"],
        )
    )


def test_typed_already_present_maps_to_zero_write_success(
    contract: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def post(_path: str, _body: dict[str, object]) -> dict[str, object]:
        raise MiaoshouAlreadyPresentObservation(72001)

    monkeypatch.setattr("modules.miaoshou.client.post_open", post)
    from domains.channel_operations.collectbox_action_adapters import (
        execute_collectbox_platform,
    )

    result = execute_collectbox_platform(
        _request(contract, platform="SHOPEE")
    )

    assert result.status == "SUCCEEDED"
    assert result.outcome == "ALREADY_PRESENT"
    assert result.platform_detail_id == "72001"
    assert result.external_writes == ()
    assert result.external_write_count == 0


def test_known_business_rejection_maps_to_retryable_zero_write(
    contract: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def post(_path: str, _body: dict[str, object]) -> dict[str, object]:
        raise MiaoshouBusinessRejectedError(
            "product missing", code="productNotFound"
        )

    monkeypatch.setattr("modules.miaoshou.client.post_open", post)
    from domains.channel_operations.collectbox_action_adapters import (
        execute_collectbox_platform,
    )

    result = execute_collectbox_platform(_request(contract))

    assert result.status == "FAILED_RETRYABLE"
    assert result.external_writes == ()
    assert result.external_write_count == 0
    assert result.error_category == "CHANNEL"
    assert result.error_code == "productNotFound"


@pytest.mark.parametrize(
    ("failure", "expected_count"),
    [
        (TimeoutError("lost after invoke"), None),
        (MiaoshouBusinessRejectedError("already", code="alreadyClaimed"), 0),
    ],
)
def test_ambiguous_or_unresolved_result_requires_reconciliation_without_retry(
    contract: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_count: int | None,
) -> None:
    calls = 0

    def post(_path: str, _body: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        raise failure

    monkeypatch.setattr("modules.miaoshou.client.post_open", post)
    from domains.channel_operations.collectbox_action_adapters import (
        execute_collectbox_platform,
    )

    result = execute_collectbox_platform(_request(contract))

    assert calls == 1
    assert result.status == "RECONCILIATION_REQUIRED"
    assert result.external_write_count == expected_count
    assert result.external_writes == (
        ("miaoshou:collectbox:claim:tiktok",)
        if expected_count is None
        else ()
    )


def test_request_identity_mismatch_stops_before_miaoshou(
    contract: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def post(_path: str, _body: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return _success("tiktok", 71001)

    monkeypatch.setattr("modules.miaoshou.client.post_open", post)
    request = _request(contract)
    request = contract.CollectBoxPlatformRequest(
        **{
            **request.__dict__,
            "common_collectbox_identity_digest": "0" * 64,
        }
    )
    from domains.channel_operations.collectbox_action_adapters import (
        execute_collectbox_platform,
    )

    result = execute_collectbox_platform(request)

    assert calls == 0
    assert result.status == "FAILED_RETRYABLE"
    assert result.external_write_count == 0
    assert result.error_category == "IDENTITY"


def test_unsupported_platform_stops_before_miaoshou(
    contract: types.ModuleType,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def post(_path: str, _body: dict[str, object]) -> dict[str, object]:
        nonlocal calls
        calls += 1
        return _success("tiktok", 71001)

    monkeypatch.setattr("modules.miaoshou.client.post_open", post)
    from domains.channel_operations.collectbox_action_adapters import (
        execute_collectbox_platform,
    )

    result = execute_collectbox_platform(_request(contract, platform="OZON"))

    assert calls == 0
    assert result.status == "FAILED_RETRYABLE"
    assert result.external_write_count == 0
    assert result.error_category == "IDENTITY"
