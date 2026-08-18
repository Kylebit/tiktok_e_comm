from __future__ import annotations

from dataclasses import replace

import pytest

from domains.channel_operations.omnichannel_orchestrator import (
    ADAPTER_NAMES,
    ChannelExecutionPlan,
    OmnichannelPublicationPlan,
    PublicationPreflight,
    SingleApprovalSummary,
)
from domains.channel_operations.release_executor import (
    AdapterExecutionResult,
    AdapterRegistration,
    ReleaseExecutionError,
    execute_release_plan,
    production_adapter_registry,
)


def _target(
    channel: str,
    site: str,
    *,
    depends_on: tuple[str, ...] = (),
) -> ChannelExecutionPlan:
    label = f"{channel}:{site}"
    return ChannelExecutionPlan(
        channel=channel,
        site=site,
        adapter=ADAPTER_NAMES[channel],
        adapter_gate_status="unified_test_adapter",
        depends_on=depends_on,
        preflight=(
            PublicationPreflight(
                code="test_ready",
                passed=True,
                detail=f"{label} is ready",
            ),
        ),
        steps=(),
        idempotency_key=f"publish:{channel}:{site}:stable-key",
        executable=True,
    )


def _plan(*, dry_run: bool = False, authorised: bool = True):
    targets = (
        _target("miaoshou", "COMMON"),
        _target(
            "tiktok",
            "LH_PH",
            depends_on=("miaoshou:COMMON:verified_draft",),
        ),
        _target(
            "tiktok",
            "LH_TH",
            depends_on=("miaoshou:COMMON:verified_draft",),
        ),
        _target(
            "shopee",
            "PH",
            depends_on=("tiktok:LH_PH:verified_readback",),
        ),
        _target(
            "shopee",
            "TH",
            depends_on=("tiktok:LH_TH:verified_readback",),
        ),
        _target(
            "ozon",
            "RU",
            depends_on=("tiktok:LH_PH:verified_readback",),
        ),
    )
    approval = SingleApprovalSummary(
        collect_box_id="3828540231",
        product_id="3828540231",
        seller_sku="0946",
        product_package_id="product:3828540231:0946",
        content_package_id="content:3828540231",
        target_labels=tuple(f"{row.channel}:{row.site}" for row in targets),
        image_count=6,
        approval_scope_digest="scope-digest-123",
        confirmation_token="PUBLISH-SCOPE123",
        irreversible_action_count=12,
        statement="Approve exact test scope.",
    )
    return OmnichannelPublicationPlan(
        targets=targets,
        approval=approval,
        plan_id="omnichannel:scope-digest-123",
        dry_run=dry_run,
        all_preflights_passed=True,
        execution_authorized=authorised,
        adapter_calls_performed=False,
    )


def _registry(handler):
    return {
        adapter_name: AdapterRegistration(
            adapter_name=adapter_name,
            execute=handler,
            consumes_unified_plan=True,
            validates_confirmation_token=True,
            preserves_idempotency_key=True,
            verifies_readback=True,
        )
        for adapter_name in ADAPTER_NAMES.values()
    }


def _record_map(report):
    return {record.target_label: record for record in report.records}


def test_all_fake_adapters_run_in_dependency_order_with_unified_request():
    requests = []

    def handler(request):
        requests.append(request)
        return AdapterExecutionResult(
            succeeded=True,
            readback_verified=True,
            detail="verified",
            external_reference=f"fake:{request.target_label}",
        )

    plan = _plan()
    report = execute_release_plan(plan, adapter_registry=_registry(handler))

    assert report.complete is True
    assert report.adapter_calls_performed == (
        "miaoshou:COMMON",
        "tiktok:LH_PH",
        "tiktok:LH_TH",
        "shopee:PH",
        "shopee:TH",
        "ozon:RU",
    )
    assert all(record.status == "SUCCESS" for record in report.records)
    assert all(record.readback_verified for record in report.records)
    assert [request.idempotency_key for request in requests] == [
        target.idempotency_key
        for target in sorted(
            plan.targets,
            key=lambda row: (
                ("miaoshou", "tiktok", "shopee", "ozon").index(row.channel),
                row.site,
            ),
        )
    ]
    assert all(
        request.confirmation_token == plan.approval.confirmation_token
        and request.approval_scope_digest
        == plan.approval.approval_scope_digest
        for request in requests
    )


def test_partial_failure_holds_same_country_shopee_but_allows_other_branches():
    calls = []

    def handler(request):
        calls.append(request.target_label)
        if request.target_label == "tiktok:LH_TH":
            return AdapterExecutionResult(
                succeeded=False,
                readback_verified=False,
                detail="TikTok TH rejected the draft",
            )
        return AdapterExecutionResult(True, True, "verified")

    report = execute_release_plan(_plan(), adapter_registry=_registry(handler))
    records = _record_map(report)

    assert report.complete is False
    assert records["tiktok:LH_TH"].status == "FAILED"
    assert records["shopee:TH"].status == "PENDING"
    assert records["shopee:TH"].attempts == 0
    assert records["shopee:TH"].blocker.code == "dependency_not_verified"
    assert records["shopee:PH"].status == "SUCCESS"
    assert records["ozon:RU"].status == "SUCCESS"
    assert "shopee:TH" not in calls


def test_retry_calls_only_failed_and_newly_unblocked_targets_with_same_key():
    attempts = {}
    requests = []

    def handler(request):
        requests.append(request)
        attempts[request.target_label] = attempts.get(request.target_label, 0) + 1
        if (
            request.target_label == "tiktok:LH_TH"
            and attempts[request.target_label] == 1
        ):
            return AdapterExecutionResult(False, False, "temporary failure")
        return AdapterExecutionResult(True, True, "verified")

    registry = _registry(handler)
    first = execute_release_plan(_plan(), adapter_registry=registry)
    first_records = _record_map(first)
    second = execute_release_plan(
        _plan(),
        adapter_registry=registry,
        prior_records=first_records,
    )

    assert second.complete is True
    assert second.adapter_calls_performed == (
        "tiktok:LH_TH",
        "shopee:TH",
    )
    retried = [
        request
        for request in requests
        if request.target_label == "tiktok:LH_TH"
    ]
    assert len(retried) == 2
    assert retried[0].idempotency_key == retried[1].idempotency_key
    assert _record_map(second)["tiktok:LH_TH"].attempts == 2
    assert _record_map(second)["miaoshou:COMMON"].attempts == 1


def test_miaoshou_failure_prevents_every_downstream_adapter_call():
    calls = []

    def handler(request):
        calls.append(request.target_label)
        return AdapterExecutionResult(False, False, "Miaoshou write failed")

    report = execute_release_plan(_plan(), adapter_registry=_registry(handler))
    records = _record_map(report)

    assert calls == ["miaoshou:COMMON"]
    assert records["miaoshou:COMMON"].status == "FAILED"
    assert all(
        records[label].status == "PENDING"
        for label in (
            "tiktok:LH_PH",
            "tiktok:LH_TH",
            "shopee:PH",
            "shopee:TH",
            "ozon:RU",
        )
    )


def test_unverified_readback_is_a_failed_attempt_not_success():
    def handler(_request):
        return AdapterExecutionResult(
            succeeded=True,
            readback_verified=False,
            detail="write returned 200 but read-back did not match",
        )

    report = execute_release_plan(_plan(), adapter_registry=_registry(handler))
    common = _record_map(report)["miaoshou:COMMON"]

    assert common.status == "FAILED"
    assert common.attempts == 1
    assert common.blocker.code == "readback_not_verified"
    assert report.adapter_calls_performed == ("miaoshou:COMMON",)


def test_accepted_submission_without_api_readback_is_terminal_not_retryable():
    def handler(request):
        if request.target_label == "miaoshou:COMMON":
            return AdapterExecutionResult(True, True, "verified common")
        return AdapterExecutionResult(
            succeeded=True,
            readback_verified=False,
            detail="accepted; target has no authorised API readback",
            external_reference="detail-1:shop-1",
            readback_evidence={"accepted": True},
            submission_accepted=True,
        )

    first = execute_release_plan(_plan(), adapter_registry=_registry(handler))
    records = _record_map(first)
    assert records["tiktok:LH_PH"].status == "SUBMITTED_UNVERIFIED"
    assert records["tiktok:LH_PH"].blocker is None

    calls = []

    def should_not_repeat(request):
        calls.append(request.target_label)
        return AdapterExecutionResult(True, True, "verified")

    second = execute_release_plan(
        _plan(),
        adapter_registry=_registry(should_not_repeat),
        prior_records=records,
    )
    assert "tiktok:LH_PH" not in second.adapter_calls_performed
    assert _record_map(second)["tiktok:LH_PH"].status == "SUBMITTED_UNVERIFIED"


def test_production_registry_blocks_all_legacy_paths_without_calling_them():
    registry = production_adapter_registry()

    assert set(registry) == set(ADAPTER_NAMES.values())
    assert all(not registration.executable for registration in registry.values())
    assert all(registration.execute is None for registration in registry.values())
    assert all(
        registration.blocker.code == "adapter_not_unified"
        for registration in registry.values()
    )
    assert "MX/GB" in registry[ADAPTER_NAMES["tiktok"]].blocker.detail

    report = execute_release_plan(_plan(), adapter_registry=registry)
    records = _record_map(report)

    assert report.adapter_calls_performed == ()
    assert records["miaoshou:COMMON"].status == "BLOCKED"
    assert records["miaoshou:COMMON"].blocker.code == "adapter_not_unified"
    assert all(
        records[label].status == "PENDING"
        for label in records
        if label != "miaoshou:COMMON"
    )


def test_empty_injected_registry_is_not_replaced_by_production_registry():
    report = execute_release_plan(_plan(), adapter_registry={})
    records = _record_map(report)

    assert report.adapter_calls_performed == ()
    assert records["miaoshou:COMMON"].status == "BLOCKED"
    assert records["miaoshou:COMMON"].blocker.code == "adapter_not_registered"


@pytest.mark.parametrize(
    "plan",
    [
        _plan(dry_run=True, authorised=False),
        _plan(dry_run=False, authorised=False),
    ],
)
def test_dry_run_or_unauthorised_plan_is_rejected_before_adapter_call(plan):
    calls = []

    def handler(request):
        calls.append(request)
        return AdapterExecutionResult(True, True, "verified")

    with pytest.raises(ReleaseExecutionError, match="authorised non-dry-run"):
        execute_release_plan(plan, adapter_registry=_registry(handler))

    assert calls == []


def test_prior_record_must_belong_to_exact_plan_and_idempotency_key():
    plan = _plan()
    success = execute_release_plan(
        plan,
        adapter_registry=_registry(
            lambda _request: AdapterExecutionResult(True, True, "verified")
        ),
    )
    records = _record_map(success)
    records["miaoshou:COMMON"] = replace(
        records["miaoshou:COMMON"],
        idempotency_key="publish:miaoshou:COMMON:wrong",
    )

    with pytest.raises(ReleaseExecutionError, match="idempotency key"):
        execute_release_plan(
            plan,
            adapter_registry=_registry(
                lambda _request: AdapterExecutionResult(True, True, "verified")
            ),
            prior_records=records,
        )
