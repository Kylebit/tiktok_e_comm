"""Channel bridge for the durable collect-box control plane."""

from __future__ import annotations

from modules.miaoshou.collectbox_claim import (
    ACCEPTED,
    ALREADY_PRESENT as CLAIM_ALREADY_PRESENT,
    CollectBoxPlatformClaimRequest,
    claim_common_collectbox_platform,
)


def _contract():
    from shared_platform.collectbox_action import (
        ALREADY_PRESENT,
        FAILED_RETRYABLE,
        IMPORTED,
        RECONCILIATION_REQUIRED,
        SUCCEEDED,
        CollectBoxPlatformRequest,
        CollectBoxPlatformResult,
        common_collectbox_identity_digest,
    )

    return {
        "ALREADY_PRESENT": ALREADY_PRESENT,
        "FAILED_RETRYABLE": FAILED_RETRYABLE,
        "IMPORTED": IMPORTED,
        "RECONCILIATION_REQUIRED": RECONCILIATION_REQUIRED,
        "SUCCEEDED": SUCCEEDED,
        "CollectBoxPlatformRequest": CollectBoxPlatformRequest,
        "CollectBoxPlatformResult": CollectBoxPlatformResult,
        "common_collectbox_identity_digest": (
            common_collectbox_identity_digest
        ),
    }


def _identity_failure(contract, *, code: str, detail: str):
    return contract["CollectBoxPlatformResult"](
        status=contract["FAILED_RETRYABLE"],
        external_writes=(),
        external_write_count=0,
        receipt_evidence={
            "schema_version": "collectbox-channel-bridge-evidence/v1",
            "identity_exact": False,
        },
        error_category="IDENTITY",
        error_code=code,
        error_detail=detail,
    )


def execute_collectbox_platform(request):
    """Execute one server-owned TikTok or Shopee platform claim."""

    contract = _contract()
    request_type = contract["CollectBoxPlatformRequest"]
    if type(request) is not request_type:
        raise TypeError("collect-box platform request type is invalid")

    platform = {"TIKTOK": "tiktok", "SHOPEE": "shopee"}.get(
        request.platform
    )
    if platform is None:
        return _identity_failure(
            contract,
            code="collectbox_platform_identity_invalid",
            detail="collect-box platform identity is invalid",
        )
    try:
        expected_common_digest = contract[
            "common_collectbox_identity_digest"
        ](request.plan_id, request.common_collect_box_detail_id)
        claim_request = CollectBoxPlatformClaimRequest(
            common_detail_id=request.common_collect_box_detail_id,
            platform=platform,
            idempotency_key=request.idempotency_key,
        )
    except (TypeError, ValueError):
        return _identity_failure(
            contract,
            code="collectbox_common_identity_invalid",
            detail="collect-box common identity is invalid",
        )
    if (
        request.common_collectbox_identity_digest != expected_common_digest
        or str(claim_request.common_detail_id)
        != request.common_collect_box_detail_id
    ):
        return _identity_failure(
            contract,
            code="collectbox_common_identity_mismatch",
            detail="collect-box common identity does not match the request",
        )

    receipt = claim_common_collectbox_platform(claim_request)
    result = receipt.result
    evidence = receipt.public_projection()
    expected_write_class = f"miaoshou:collectbox:claim:{platform}"
    if result.write_class != expected_write_class:
        raise ValueError("collect-box platform write class drifted")

    if result.status == ACCEPTED:
        if result.platform_detail_id is None:
            raise ValueError("accepted collect-box result has no identity")
        return contract["CollectBoxPlatformResult"](
            status=contract["SUCCEEDED"],
            outcome=contract["IMPORTED"],
            platform_detail_id=str(result.platform_detail_id),
            external_writes=(expected_write_class,),
            external_write_count=1,
            receipt_evidence=evidence,
        )
    if result.status == CLAIM_ALREADY_PRESENT:
        if result.platform_detail_id is None:
            raise ValueError("existing collect-box result has no identity")
        return contract["CollectBoxPlatformResult"](
            status=contract["SUCCEEDED"],
            outcome=contract["ALREADY_PRESENT"],
            platform_detail_id=str(result.platform_detail_id),
            external_writes=(),
            external_write_count=0,
            receipt_evidence=evidence,
        )
    if result.retry_safe and not result.reconciliation_required:
        return contract["CollectBoxPlatformResult"](
            status=contract["FAILED_RETRYABLE"],
            external_writes=(),
            external_write_count=0,
            receipt_evidence=evidence,
            error_category="CHANNEL",
            error_code=result.reason_code,
            error_detail="Miaoshou rejected the collect-box claim before write",
        )
    if result.reconciliation_required:
        unknown = result.write_outcome == "UNKNOWN"
        return contract["CollectBoxPlatformResult"](
            status=contract["RECONCILIATION_REQUIRED"],
            external_writes=(expected_write_class,) if unknown else (),
            external_write_count=None if unknown else 0,
            receipt_evidence=evidence,
            error_category="UNKNOWN" if unknown else "CHANNEL",
            error_code=result.reason_code,
            error_detail=(
                "Miaoshou collect-box claim outcome is unknown"
                if unknown
                else "Miaoshou collect-box identity requires reconciliation"
            ),
        )
    raise ValueError("collect-box platform result is not mappable")
