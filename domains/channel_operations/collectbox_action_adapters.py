"""Channel bridge for the durable collect-box control plane."""

from __future__ import annotations

from collections.abc import Mapping

from modules.miaoshou.collectbox_claim import (
    ACCEPTED,
    ALREADY_PRESENT as CLAIM_ALREADY_PRESENT,
    CollectBoxPlatformClaimRequest,
    claim_common_collectbox_platform,
)
from modules.miaoshou.oneclick_release import (
    MiaoshouCollectBoxPreparationError,
    prepare_selected_platform_collectbox,
)


def _contract():
    from shared_platform.collectbox_action import (
        ALREADY_PRESENT,
        FAILED,
        FAILED_RETRYABLE,
        IMPORTED,
        RECONCILIATION_REQUIRED,
        REPAIRED_SUCCEEDED,
        SUCCEEDED,
        CollectBoxPlatformRequest,
        CollectBoxPlatformResult,
        CollectBoxTargetOutcome,
        common_collectbox_identity_digest,
    )

    return {
        "ALREADY_PRESENT": ALREADY_PRESENT,
        "FAILED": FAILED,
        "FAILED_RETRYABLE": FAILED_RETRYABLE,
        "IMPORTED": IMPORTED,
        "RECONCILIATION_REQUIRED": RECONCILIATION_REQUIRED,
        "REPAIRED_SUCCEEDED": REPAIRED_SUCCEEDED,
        "SUCCEEDED": SUCCEEDED,
        "CollectBoxPlatformRequest": CollectBoxPlatformRequest,
        "CollectBoxPlatformResult": CollectBoxPlatformResult,
        "CollectBoxTargetOutcome": CollectBoxTargetOutcome,
        "common_collectbox_identity_digest": (
            common_collectbox_identity_digest
        ),
    }


def _typed_target_outcomes(contract, prepared_targets, expected_targets):
    if not isinstance(prepared_targets, list):
        return ()
    if not prepared_targets:
        raise ValueError("prepared target outcomes are empty")
    required_keys = {
        "target_label",
        "status",
        "error_code",
        "detail_digest",
    }
    if any(
        not isinstance(row, Mapping) or set(row) != required_keys
        for row in prepared_targets
    ):
        raise ValueError("prepared target outcomes are invalid")
    outcomes = tuple(
        contract["CollectBoxTargetOutcome"](
            target_label=row["target_label"],
            status=row["status"],
            error_code=row["error_code"],
            detail_digest=row["detail_digest"],
        )
        for row in prepared_targets
    )
    if tuple(outcome.target_label for outcome in outcomes) != expected_targets:
        raise ValueError("prepared target outcome identity drifted")
    return outcomes


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
    """Claim and configure selected platform drafts; never publish them."""

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
    claim_evidence = receipt.public_projection()
    expected_write_class = f"miaoshou:collectbox:claim:{platform}"
    if result.write_class != expected_write_class:
        raise ValueError("collect-box platform write class drifted")

    if result.status == ACCEPTED:
        if result.platform_detail_id is None:
            raise ValueError("accepted collect-box result has no identity")
        initial_claim_written = True
    elif result.status == CLAIM_ALREADY_PRESENT:
        if result.platform_detail_id is None:
            raise ValueError("existing collect-box result has no identity")
        initial_claim_written = False
    elif result.retry_safe and not result.reconciliation_required:
        return contract["CollectBoxPlatformResult"](
            status=contract["FAILED_RETRYABLE"],
            external_writes=(),
            external_write_count=0,
            receipt_evidence=claim_evidence,
            error_category="CHANNEL",
            error_code=result.reason_code,
            error_detail="Miaoshou rejected the collect-box claim before write",
        )
    elif result.reconciliation_required:
        unknown = result.write_outcome == "UNKNOWN"
        return contract["CollectBoxPlatformResult"](
            status=contract["RECONCILIATION_REQUIRED"],
            external_writes=(expected_write_class,) if unknown else (),
            external_write_count=None if unknown else 0,
            receipt_evidence=claim_evidence,
            error_category="UNKNOWN" if unknown else "CHANNEL",
            error_code=result.reason_code,
            error_detail=(
                "Miaoshou collect-box claim outcome is unknown"
                if unknown
                else "Miaoshou collect-box identity requires reconciliation"
            ),
        )
    else:
        raise ValueError("collect-box platform result is not mappable")

    try:
        prepared = prepare_selected_platform_collectbox(
            platform=platform,
            common_detail_id=request.common_collect_box_detail_id,
            initial_platform_detail_id=str(result.platform_detail_id),
            initial_claim_written=initial_claim_written,
            approved_plan_payload=request.approved_plan_payload,
            approved_targets=request.approved_targets,
        )
    except MiaoshouCollectBoxPreparationError as error:
        writes = tuple(error.external_writes)
        count = error.external_write_count
        status = (
            contract["FAILED_RETRYABLE"]
            if count == 0 and not writes
            else contract["RECONCILIATION_REQUIRED"]
        )
        return contract["CollectBoxPlatformResult"](
            status=status,
            external_writes=writes,
            external_write_count=count,
            target_statuses=tuple(error.target_results),
            receipt_evidence={
                "schema_version": "collectbox-platform-preparation-evidence/v1",
                "platform": platform,
                "target_configuration_exact": False,
                "claim_result_digest": claim_evidence["result"]["evidence_digest"],
            },
            error_category=(
                "CHANNEL"
                if status == contract["FAILED_RETRYABLE"]
                else "UNKNOWN"
            ),
            error_code="collectbox_platform_preparation_failed",
            error_detail="Miaoshou platform collect-box preparation failed",
        )

    writes = tuple(prepared["external_writes"])
    raw_count = prepared["external_write_count"]
    count = int(raw_count) if raw_count is not None else None
    prepared_targets = prepared.get("target_results")
    selected_targets = tuple(
        target
        for target in request.approved_targets
        if target.startswith(f"{platform}:")
    )
    target_outcomes = _typed_target_outcomes(
        contract, prepared_targets, selected_targets
    )
    target_statuses = (
        ()
        if target_outcomes
        else tuple(
            (target, "SUCCEEDED")
            for target in selected_targets
        )
    )
    partial = (
        any(
            outcome.status == contract["FAILED"]
            for outcome in target_outcomes
        )
        if target_outcomes
        else any(
            status != contract["SUCCEEDED"]
            for _target, status in target_statuses
        )
    )
    if partial:
        status = (
            contract["FAILED_RETRYABLE"]
            if count == 0 and not writes
            else contract["RECONCILIATION_REQUIRED"]
        )
        return contract["CollectBoxPlatformResult"](
            status=status,
            external_writes=writes,
            external_write_count=count,
            target_statuses=target_statuses,
            target_outcomes=target_outcomes,
            receipt_evidence={
                "schema_version": "collectbox-platform-preparation-evidence/v1",
                "platform": platform,
                "target_count": prepared["target_count"],
                "platform_detail_count": prepared["platform_detail_count"],
                "checks": prepared["checks"],
                "claim_result_digest": claim_evidence["result"]["evidence_digest"],
            },
            error_category=(
                "CHANNEL"
                if status == contract["FAILED_RETRYABLE"]
                else "UNKNOWN"
            ),
            error_code="collectbox_platform_preparation_partial",
            error_detail="one or more Miaoshou target drafts failed preparation",
        )
    return contract["CollectBoxPlatformResult"](
        status=contract["SUCCEEDED"],
        outcome=(
            contract["IMPORTED"]
            if count > 0
            else contract["ALREADY_PRESENT"]
        ),
        platform_detail_id=str(prepared["primary_platform_detail_id"]),
        external_writes=writes,
        external_write_count=count,
        target_statuses=target_statuses,
        target_outcomes=target_outcomes,
        receipt_evidence={
            "schema_version": "collectbox-platform-preparation-evidence/v1",
            "platform": platform,
            "target_count": prepared["target_count"],
            "platform_detail_count": prepared["platform_detail_count"],
            "checks": prepared["checks"],
            "result": claim_evidence["result"],
            "claim_result_digest": claim_evidence["result"]["evidence_digest"],
        },
    )
