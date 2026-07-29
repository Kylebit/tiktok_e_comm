import hashlib
import json
import sqlite3
import threading

import pytest

from domains.product_operations import (
    ModelSkuAssignment,
    SkuAssignment,
    finalize_new_source_sku_reservation,
    resolve_source_product_identity,
)
from shared_platform.oneclick_release_controlplane import (
    BLOCKED_CAPABILITY,
    BLOCKED_INVENTORY,
    EXACT_READY_AUTOMATIC,
    FAILED_PRE_SUBMIT,
    READY_SUBMIT_MANUAL,
    RECONCILIATION_REQUIRED,
    SUBMITTED_UNVERIFIED,
    SUCCEEDED,
    AdapterRegistration,
    AdapterContractError,
    DispatchInvocationError,
    DispatchTargetResult,
    OneClickReleaseStore,
    OneClickReleaseWorker,
    PrepareTargetResult,
    PreDispatchInvocationError,
    SystemicIdentityError,
    build_batch_preview,
    preview_run_for_plan,
)
from shared_platform.release_store import ReleaseStore


def _digest(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _source_identity(source_offer_id="168812345"):
    resolution = resolve_source_product_identity(
        collect_box={
            "source_item_id": source_offer_id,
            "source_item_code": "DISPLAY-ONLY-001",
        },
        precollect={
            "records": [
                {
                    "source_id": source_offer_id,
                    "source_item_code": "DISPLAY-ONLY-001",
                }
            ]
        },
        source_authority="1688",
    )
    assert resolution.ready
    return resolution.identity.payload()


def _plan_payload(*, targets, identity=None, inventory_ready=False):
    identity = identity or _source_identity()
    source = resolve_source_product_identity(
        collect_box={
            "source_item_id": identity["source_offer_id"],
            "source_item_code": identity.get("source_item_code"),
        },
        precollect={
            "records": [
                {
                    "source_id": identity["source_offer_id"],
                    "source_item_code": identity.get("source_item_code"),
                }
            ]
        },
        source_authority=identity["source_authority"],
    )
    assert source.ready and source.identity is not None
    assignment_contract = SkuAssignment(
        seller_sku="0954",
        model_skus=(
            ModelSkuAssignment(
                variant_key="default",
                model_sku="0954",
            ),
        ),
    )
    finalized = finalize_new_source_sku_reservation(
        source_identity=source.identity,
        assignment=assignment_contract,
    )
    assert finalized.ready and finalized.reservation is not None
    assignment = assignment_contract.payload()
    payload = {
        "plan_id": "omnichannel:oneclick-test",
        "product_id": "3838616043",
        "seller_sku": "0954",
        "product_package_id": "product:3838616043:0954",
        "content_package_id": "content:3838616043:r31",
        "targets": list(targets),
        "product_revision": 31,
        "source_product_identity": identity,
        "sku_lineage": {
            "schema_version": "sku-lineage-reservation/v1",
            "status": "READY",
            "ready": True,
            "source_identity_digest": identity["identity_digest"],
            "lineage_mode": "NEW_SOURCE",
            "assignment": assignment,
            "predecessor_id": None,
            "predecessor_revision": None,
            "predecessor_digest": None,
            "reservation": finalized.reservation.payload(),
            "blockers": [],
        },
        "commercial_scope": {"policy": "test-only"},
    }
    if inventory_ready:
        payload["approved_inventory_decisions"] = {
            "ozon:RU": {
                "schema_version": "approved-sellable-inventory-decision/v1",
                "status": "READY",
                "quantity": 7,
            }
        }
    return payload


def _approved_context(tmp_path, *, targets, inventory_ready=False):
    release = ReleaseStore(tmp_path / "release.db")
    created = release.create_plan(
        _plan_payload(targets=targets, inventory_ready=inventory_ready)
    )
    release.approve_plan(
        created["plan_id"],
        approved_by="Kyle",
        user_approved=True,
        confirmation_token=created["confirmation_token"],
    )
    plan = release.get_plan(created["plan_id"])
    run = release.start_run(created["plan_id"])
    return release, plan, run


def _registry(
    targets,
    *,
    prepare_calls=None,
    dispatch_calls=None,
    prepare_override=None,
    dispatch_override=None,
    manual_labels=(),
):
    prepare_calls = prepare_calls if prepare_calls is not None else []
    dispatch_calls = dispatch_calls if dispatch_calls is not None else []
    by_adapter = {}
    for label in targets:
        channel = label.split(":", 1)[0]
        adapter_name = {
            "miaoshou": "new_product_workbench_miaoshou_commit",
            "tiktok": "miaoshou_tiktok_publish",
            "shopee": "shopee_cnsc_publish",
            "ozon": "ozon_product_publish",
        }[channel]
        by_adapter.setdefault(adapter_name, []).append(label)

    result = {}
    for adapter_name, labels in by_adapter.items():
        def prepare(request, _labels=tuple(labels)):
            prepare_calls.append(request.target_label)
            if prepare_override:
                return prepare_override(request)
            manual = request.target_label in manual_labels
            return PrepareTargetResult(
                classification=(
                    READY_SUBMIT_MANUAL if manual else EXACT_READY_AUTOMATIC
                ),
                reason_category="CAPABILITY",
                reason_scope="TARGET",
                reason_code="official_proof_exact",
                reason_detail="official read-only proof is exact",
                command={"kind": "fixture", "target": request.target_label},
                proof={"kind": "fixture-proof", "target": request.target_label},
                manual_after_submit=manual,
            )

        def dispatch(request):
            dispatch_calls.append(request.target_label)
            if dispatch_override:
                return dispatch_override(request)
            return DispatchTargetResult(
                canonical_status=SUCCEEDED,
                reason_category="CAPABILITY",
                reason_scope="TARGET",
                reason_code="official_readback_exact",
                reason_detail="official readback is exact",
                external_writes=(f"{request.target_label}:write",),
                external_id=f"internal-{request.target_label}",
                submission_accepted=True,
                readback_verified=True,
                evidence={"checks": {"identity": True}},
            )

        result[adapter_name] = AdapterRegistration(
            adapter_name=adapter_name,
            target_labels=tuple(labels),
            prepare=prepare,
            dispatch=dispatch,
            policy_digest=_digest(adapter_name),
            prepare_is_read_only=True,
            consumes_prepared_command=True,
            preserves_idempotency_key=True,
            reports_truthful_receipt=True,
        )
    return result


def test_all_targets_prepare_before_first_atomic_claim(tmp_path):
    targets = ["shopee:PH", "shopee:MY"]
    _release, plan, run = _approved_context(tmp_path, targets=targets)
    calls = []
    registry = _registry(targets, prepare_calls=calls)
    control = OneClickReleaseStore(tmp_path / "release.db")
    job = control.ensure_job(
        plan=plan, run=run, product_revision=31, registry=registry
    )

    prepared = control.prepare_job(job["job_id"], registry)
    assert calls == targets
    assert prepared["summary"]["will_dispatch"] == targets
    request = control.claim_next_dispatch(job["job_id"], registry)
    assert request.target_label == "shopee:PH"
    assert calls == targets


def test_systemic_prepare_error_stops_whole_batch_before_claim(tmp_path):
    targets = ["shopee:PH", "shopee:MY"]
    _release, plan, run = _approved_context(tmp_path, targets=targets)

    def prepare(request):
        if request.target_label == "shopee:MY":
            raise ValueError("official identity shape drifted")
        return PrepareTargetResult(
            classification=EXACT_READY_AUTOMATIC,
            reason_category="CAPABILITY",
            reason_scope="TARGET",
            reason_code="ready",
            reason_detail="ready",
            command={"target": request.target_label},
            proof={"target": request.target_label},
        )

    registry = _registry(targets, prepare_override=prepare)
    control = OneClickReleaseStore(tmp_path / "release.db")
    job = control.ensure_job(
        plan=plan, run=run, product_revision=31, registry=registry
    )
    stopped = control.prepare_job(job["job_id"], registry)

    assert stopped["phase"] == "SYSTEMIC_STOPPED"
    assert control.claim_next_dispatch(job["job_id"], registry) is None
    assert ReleaseStore(tmp_path / "release.db").get_run(run["run_id"])[
        "targets"
    ][0]["attempts"] == 0


def test_concurrent_claim_is_exactly_once_across_both_ledgers(tmp_path):
    targets = ["shopee:PH"]
    _release, plan, run = _approved_context(tmp_path, targets=targets)
    registry = _registry(targets)
    control = OneClickReleaseStore(tmp_path / "release.db")
    job = control.ensure_job(
        plan=plan, run=run, product_revision=31, registry=registry
    )
    control.prepare_job(job["job_id"], registry)
    claimed = []

    def claim():
        claimed.append(control.claim_next_dispatch(job["job_id"], registry))

    threads = [threading.Thread(target=claim) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(value is not None for value in claimed) == 1
    target = ReleaseStore(tmp_path / "release.db").get_run(run["run_id"])[
        "targets"
    ][0]
    assert target["status"] == "RUNNING"
    assert target["attempts"] == 1


@pytest.mark.parametrize(
    "result,expected_control,expected_physical",
    [
        (
            DispatchTargetResult(
                canonical_status=SUCCEEDED,
                reason_category="CAPABILITY",
                reason_scope="TARGET",
                reason_code="verified",
                reason_detail="verified",
                external_writes=("shopee:regional_publish",),
                external_id="internal-result",
                submission_accepted=True,
                readback_verified=True,
            ),
            SUCCEEDED,
            "SUCCEEDED",
        ),
        (
            DispatchTargetResult(
                canonical_status=SUBMITTED_UNVERIFIED,
                reason_category="POST_WRITE",
                reason_scope="TARGET",
                reason_code="accepted_unverified",
                reason_detail="accepted without official readback",
                external_writes=("tiktok:publish",),
                external_id="internal-result",
                submission_accepted=True,
            ),
            SUBMITTED_UNVERIFIED,
            "SUBMITTED_UNVERIFIED",
        ),
        (
            DispatchTargetResult(
                canonical_status=FAILED_PRE_SUBMIT,
                reason_category="PRE_SUBMIT",
                reason_scope="TARGET",
                reason_code="zero_write_block",
                reason_detail="blocked before dispatch",
                external_writes=(),
            ),
            FAILED_PRE_SUBMIT,
            "FAILED",
        ),
        (
            DispatchTargetResult(
                canonical_status=RECONCILIATION_REQUIRED,
                reason_category="POST_WRITE",
                reason_scope="TARGET",
                reason_code="unknown_after_write",
                reason_detail="official readback timed out",
                external_writes=("shopee:regional_publish",),
                dispatch_outcome_unknown=True,
            ),
            RECONCILIATION_REQUIRED,
            "FAILED",
        ),
    ],
)
def test_terminal_receipts_keep_control_and_canonical_ledgers_consistent(
    tmp_path, result, expected_control, expected_physical
):
    targets = ["shopee:PH"]
    _release, plan, run = _approved_context(tmp_path, targets=targets)
    registry = _registry(targets)
    control = OneClickReleaseStore(tmp_path / "release.db")
    job = control.ensure_job(
        plan=plan, run=run, product_revision=31, registry=registry
    )
    control.prepare_job(job["job_id"], registry)
    request = control.claim_next_dispatch(job["job_id"], registry)
    control.record_dispatch_result(request, result)

    public = control.get_job(job_id=job["job_id"])
    assert public["targets"][0]["status"] == expected_control
    physical = ReleaseStore(tmp_path / "release.db").get_run(run["run_id"])[
        "targets"
    ][0]
    assert physical["status"] == expected_physical


def test_composite_shopee_write_ledger_survives_later_exception_and_restart(
    tmp_path,
):
    targets = ["shopee:MY"]
    _release, plan, run = _approved_context(tmp_path, targets=targets)

    def dispatch(request):
        request.progress_recorder(
            request,
            ("shopee:global_master:update",),
            "global_master_confirmed",
            {"verified": True},
        )
        request.progress_recorder(
            request,
            ("shopee:regional_publish",),
            "regional_publish_invoked",
            {"accepted": True},
        )
        raise RuntimeError("regional readback parser failed")

    registry = _registry(targets, dispatch_override=dispatch)
    control = OneClickReleaseStore(tmp_path / "release.db")
    job = control.ensure_job(
        plan=plan, run=run, product_revision=31, registry=registry
    )
    worker = OneClickReleaseWorker(
        control, lambda: registry, dispatch_enabled=lambda: True
    )
    assert worker.advance_once(job["job_id"]) is True
    assert worker.advance_once(job["job_id"]) is True

    target = control.get_job(job_id=job["job_id"])["targets"][0]
    assert target["status"] == RECONCILIATION_REQUIRED
    assert target["result"]["cumulative_external_write_classes"] == [
        "shopee:global_master:update",
        "shopee:regional_publish",
        "UNKNOWN",
    ]
    assert control.recover_interrupted_dispatches() == 0


def test_worker_restart_recovers_claim_without_redispatch(tmp_path):
    targets = ["shopee:PH"]
    _release, plan, run = _approved_context(tmp_path, targets=targets)
    dispatch_calls = []
    registry = _registry(targets, dispatch_calls=dispatch_calls)
    control = OneClickReleaseStore(tmp_path / "release.db")
    job = control.ensure_job(
        plan=plan, run=run, product_revision=31, registry=registry
    )
    control.prepare_job(job["job_id"], registry)
    request = control.claim_next_dispatch(job["job_id"], registry)
    request = request.__class__(**{**request.__dict__, "progress_recorder": control.record_dispatch_progress})
    control.record_dispatch_progress(
        request,
        ("shopee:global_master:update",),
        "global_master_confirmed",
        {"verified": True},
    )

    assert control.recover_interrupted_dispatches() == 1
    worker = OneClickReleaseWorker(
        control, lambda: registry, dispatch_enabled=lambda: True
    )
    assert worker.advance_once(job["job_id"]) is False
    assert dispatch_calls == []
    target = control.get_job(job_id=job["job_id"])["targets"][0]
    assert target["status"] == RECONCILIATION_REQUIRED
    assert target["dispatch_ledger"]["cumulative_external_write_classes"] == [
        "shopee:global_master:update",
        "UNKNOWN",
    ]


def test_ozon_without_approved_inventory_is_blocked_and_not_denominator_ready(
    tmp_path,
):
    targets = ["ozon:RU"]
    _release, plan, run = _approved_context(tmp_path, targets=targets)
    prepare_calls = []
    registry = _registry(targets, prepare_calls=prepare_calls)
    preview = build_batch_preview(
        plan=plan,
        run=preview_run_for_plan(plan),
        product_revision=31,
        registry=registry,
    )

    assert preview["will_dispatch"] == []
    assert preview["blocked"] == ["ozon:RU"]
    assert preview["targets"][0]["status"] == BLOCKED_INVENTORY
    assert prepare_calls == []


def test_eleven_storefront_matrix_excludes_common_control_row(tmp_path):
    targets = [
        "miaoshou:COMMON",
        "tiktok:MX",
        "tiktok:GB",
        "tiktok:HB_PH",
        "tiktok:HB_MY",
        "tiktok:HB_TH",
        "tiktok:HB_VN",
        "shopee:PH",
        "shopee:MY",
        "shopee:TH",
        "shopee:VN",
        "ozon:RU",
    ]
    _release, plan, _run = _approved_context(tmp_path, targets=targets)
    registry = _registry(targets)
    preview = build_batch_preview(
        plan=plan,
        run=preview_run_for_plan(plan),
        product_revision=31,
        registry=registry,
    )

    assert preview["storefront_count"] == 11
    assert preview["control_row_count"] == 1
    assert preview["blocked"] == ["ozon:RU"]
    assert preview["runnable_target_count"] == 4
    assert preview["will_dispatch"] == [
        "shopee:PH",
        "shopee:MY",
        "shopee:TH",
        "shopee:VN",
    ]


def test_source_identity_and_sku_reservation_drift_block_before_claim(tmp_path):
    targets = ["shopee:PH"]
    release, plan, run = _approved_context(tmp_path, targets=targets)
    registry = _registry(targets)
    control = OneClickReleaseStore(tmp_path / "release.db")
    job = control.ensure_job(
        plan=plan, run=run, product_revision=31, registry=registry
    )
    control.prepare_job(job["job_id"], registry)

    with sqlite3.connect(release.path) as connection:
        connection.execute(
            """
            UPDATE release_sku_reservations
            SET status = 'SUPERSEDED'
            WHERE plan_id = ?
            """,
            (plan["plan_id"],),
        )
        connection.execute(
            """
            UPDATE release_source_sku_reservations
            SET status = 'SUPERSEDED'
            WHERE reservation_digest = (
                SELECT reservation_digest
                FROM release_source_sku_plan_links
                WHERE plan_id = ?
            )
            """,
            (plan["plan_id"],),
        )
    with pytest.raises(SystemicIdentityError, match="SKU reservation"):
        control.claim_next_dispatch(job["job_id"], registry)
    assert release.get_run(run["run_id"])["targets"][0]["attempts"] == 0


def test_public_projection_redacts_command_source_and_external_identity(tmp_path):
    targets = ["shopee:PH"]
    _release, plan, run = _approved_context(tmp_path, targets=targets)
    registry = _registry(targets)
    control = OneClickReleaseStore(tmp_path / "release.db")
    job = control.ensure_job(
        plan=plan, run=run, product_revision=31, registry=registry
    )
    public = control.prepare_job(job["job_id"], registry)
    encoded = json.dumps(public, ensure_ascii=False)

    for forbidden in (
        "168812345",
        "fixture-proof",
        "\"command\"",
        "internal-result",
    ):
        assert forbidden not in encoded


def test_common_blocker_makes_tiktok_non_runnable_and_job_blocked(tmp_path):
    targets = ["miaoshou:COMMON", "tiktok:MX"]
    release, plan, run = _approved_context(tmp_path, targets=targets)
    dispatch_calls = []

    def prepare(request):
        if request.target_label == "miaoshou:COMMON":
            return PrepareTargetResult(
                classification=BLOCKED_CAPABILITY,
                reason_category="CAPABILITY",
                reason_scope="TARGET",
                reason_code="common_manual_action_required",
                reason_detail="COMMON requires a governed safe action",
            )
        return PrepareTargetResult(
            classification=EXACT_READY_AUTOMATIC,
            reason_category="CAPABILITY",
            reason_scope="TARGET",
            reason_code="official_proof_exact",
            reason_detail="official proof is exact",
            command={"target": request.target_label},
            proof={"target": request.target_label},
        )

    registry = _registry(
        targets,
        prepare_override=prepare,
        dispatch_calls=dispatch_calls,
    )
    preview = build_batch_preview(
        plan=plan,
        run=preview_run_for_plan(plan),
        product_revision=31,
        registry=registry,
    )
    assert preview["runnable_target_count"] == 0
    assert preview["will_dispatch"] == []
    tiktok_preview = next(
        row for row in preview["targets"]
        if row["target_label"] == "tiktok:MX"
    )
    assert tiktok_preview["dependency"]["state"] == "BLOCKED"
    assert tiktok_preview["next_action_target"] == "miaoshou:COMMON"

    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan,
        run=run,
        product_revision=31,
        registry=registry,
    )
    projected = control.prepare_job(job["job_id"], registry)
    assert projected["phase"] == "BLOCKED"
    assert projected["runnable_target_count"] == 0
    assert control.claim_next_dispatch(job["job_id"], registry) is None
    assert dispatch_calls == []
    assert release.get_run(run["run_id"])["targets"][0]["attempts"] == 0


@pytest.mark.parametrize(
    ("reason_category", "expected_action"),
    [
        ("CONTENT", "review_approved_content_facts"),
        ("LOGISTICS", "review_logistics_policy"),
        ("CAPABILITY", "wait_for_channel_capability"),
        ("SYSTEMIC_CONTRACT", "wait_for_channel_capability"),
    ],
)
def test_blocked_capability_next_action_uses_reason_category(
    tmp_path,
    reason_category,
    expected_action,
):
    targets = ["shopee:MY"]
    release, plan, run = _approved_context(tmp_path, targets=targets)

    def prepare(request):
        return PrepareTargetResult(
            classification=BLOCKED_CAPABILITY,
            reason_category=reason_category,
            reason_scope="TARGET",
            reason_code="blocked_prepare_fixture",
            reason_detail="blocked by a governed preparation fact",
        )

    registry = _registry(targets, prepare_override=prepare)
    preview = build_batch_preview(
        plan=plan,
        run=preview_run_for_plan(plan),
        product_revision=31,
        registry=registry,
    )
    assert preview["targets"][0]["next_action"] == expected_action
    assert preview["targets"][0]["next_action_target"] == "shopee:MY"

    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan,
        run=run,
        product_revision=31,
        registry=registry,
    )
    projected = control.prepare_job(job["job_id"], registry)
    assert projected["targets"][0]["next_action"] == expected_action
    assert projected["targets"][0]["next_action_target"] == "shopee:MY"
    assert control.claim_next_dispatch(job["job_id"], registry) is None


def test_tiktok_without_common_is_systemic_dependency_block_zero_claim(tmp_path):
    targets = ["tiktok:MX"]
    release, plan, run = _approved_context(tmp_path, targets=targets)
    dispatch_calls = []
    registry = _registry(targets, dispatch_calls=dispatch_calls)
    preview = build_batch_preview(
        plan=plan,
        run=preview_run_for_plan(plan),
        product_revision=31,
        registry=registry,
    )
    target = preview["targets"][0]
    assert preview["runnable_target_count"] == 0
    assert target["dependency"]["reason_category"] == "SYSTEMIC_CONTRACT"
    assert target["dependency"]["reason_code"] == (
        "required_common_control_target_missing"
    )

    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan,
        run=run,
        product_revision=31,
        registry=registry,
    )
    projected = control.prepare_job(job["job_id"], registry)
    assert projected["phase"] == "BLOCKED"
    assert control.claim_next_dispatch(job["job_id"], registry) is None
    assert dispatch_calls == []
    assert release.get_run(run["run_id"])["targets"][0]["attempts"] == 0


@pytest.mark.parametrize(
    "field,value",
    [
        ("classification", True),
        ("classification", 1),
        ("reason_category", {"value": "CAPABILITY"}),
        ("reason_scope", False),
        ("reason_code", 7),
        ("reason_detail", ["raw"]),
        ("manual_after_submit", "false"),
        ("command", 1),
        ("proof", []),
    ],
)
def test_prepare_result_mapping_rejects_implicit_type_coercion(field, value):
    mapping = {
        "classification": EXACT_READY_AUTOMATIC,
        "reason_category": "CAPABILITY",
        "reason_scope": "TARGET",
        "reason_code": "ready",
        "reason_detail": "ready",
        "command": {"kind": "fixture"},
        "proof": {"kind": "fixture"},
        "manual_after_submit": False,
    }
    mapping[field] = value
    with pytest.raises(AdapterContractError):
        PrepareTargetResult.from_value(mapping)


@pytest.mark.parametrize(
    "field,value",
    [
        ("canonical_status", 1),
        ("reason_category", True),
        ("reason_scope", {"scope": "TARGET"}),
        ("reason_code", False),
        ("reason_detail", 9),
        ("external_writes", [1]),
        ("external_writes", [True]),
        ("external_writes", [{"write": "x"}]),
        ("external_id", 123),
        ("submission_accepted", 1),
        ("readback_verified", "true"),
        ("dispatch_outcome_unknown", 0),
        ("evidence", "raw response"),
    ],
)
def test_dispatch_result_mapping_rejects_implicit_type_coercion(field, value):
    mapping = {
        "canonical_status": SUCCEEDED,
        "reason_category": "CAPABILITY",
        "reason_scope": "TARGET",
        "reason_code": "verified",
        "reason_detail": "verified",
        "external_writes": ["shopee:regional_publish"],
        "external_id": "internal-id",
        "submission_accepted": True,
        "readback_verified": True,
        "dispatch_outcome_unknown": False,
    }
    mapping[field] = value
    with pytest.raises(AdapterContractError):
        DispatchTargetResult.from_value(mapping)


@pytest.mark.parametrize(
    "mapping",
    [
        {
            "canonical_status": SUCCEEDED,
            "external_writes": ["UNKNOWN"],
            "external_id": "id",
            "submission_accepted": True,
            "readback_verified": True,
            "dispatch_outcome_unknown": True,
        },
        {
            "canonical_status": SUBMITTED_UNVERIFIED,
            "external_writes": ["UNKNOWN"],
            "external_id": "id",
            "submission_accepted": True,
            "readback_verified": False,
            "dispatch_outcome_unknown": True,
        },
        {
            "canonical_status": FAILED_PRE_SUBMIT,
            "external_writes": [],
            "external_id": "impossible",
            "submission_accepted": False,
            "readback_verified": False,
            "dispatch_outcome_unknown": False,
        },
        {
            "canonical_status": RECONCILIATION_REQUIRED,
            "external_writes": ["shopee:regional_publish"],
            "external_id": None,
            "submission_accepted": False,
            "readback_verified": True,
            "dispatch_outcome_unknown": False,
        },
        {
            "canonical_status": BLOCKED_CAPABILITY,
            "external_writes": [],
            "external_id": None,
            "submission_accepted": True,
            "readback_verified": False,
            "dispatch_outcome_unknown": False,
        },
        {
            "canonical_status": "BLOCKED_SOURCE_IDENTITY",
            "external_writes": [],
            "external_id": None,
            "submission_accepted": False,
            "readback_verified": False,
            "dispatch_outcome_unknown": False,
        },
    ],
)
def test_dispatch_result_rejects_contradictory_terminal_truth(mapping):
    value = {
        "reason_category": "CAPABILITY",
        "reason_scope": "TARGET",
        "reason_code": "terminal",
        "reason_detail": "terminal",
        **mapping,
    }
    with pytest.raises(AdapterContractError):
        DispatchTargetResult.from_value(value)


def test_dispatch_boundary_only_dedicated_preinvoke_error_is_zero_write(
    tmp_path,
):
    targets = ["shopee:PH"]
    release, plan, run = _approved_context(tmp_path, targets=targets)

    def dispatch_preinvoke(_request):
        raise PreDispatchInvocationError("credential unavailable before invoke")

    registry = _registry(targets, dispatch_override=dispatch_preinvoke)
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan, run=run, product_revision=31, registry=registry
    )
    worker = OneClickReleaseWorker(
        control, lambda: registry, dispatch_enabled=lambda: True
    )
    assert worker.advance_once(job["job_id"]) is True
    assert worker.advance_once(job["job_id"]) is True
    target = control.get_job(job_id=job["job_id"])["targets"][0]
    assert target["status"] == FAILED_PRE_SUBMIT
    assert target["result"]["external_write_count"] == 0


@pytest.mark.parametrize(
    "exception",
    [
        RuntimeError("ordinary dispatch failure"),
        DispatchInvocationError("typed but invocation boundary was crossed"),
    ],
)
def test_any_post_invocation_exception_is_unknown_reconciliation(
    tmp_path,
    exception,
):
    targets = ["shopee:PH"]
    release, plan, run = _approved_context(tmp_path, targets=targets)

    def dispatch(_request):
        raise exception

    registry = _registry(targets, dispatch_override=dispatch)
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan, run=run, product_revision=31, registry=registry
    )
    worker = OneClickReleaseWorker(
        control, lambda: registry, dispatch_enabled=lambda: True
    )
    worker.advance_once(job["job_id"])
    worker.advance_once(job["job_id"])
    target = control.get_job(job_id=job["job_id"])["targets"][0]
    assert target["status"] == RECONCILIATION_REQUIRED
    assert target["result"]["external_write_count"] is None
    assert target["result"]["external_write_classes"] == ["UNKNOWN"]


def test_known_write_plus_unknown_reports_null_count_in_all_receipts(tmp_path):
    targets = ["shopee:MY"]
    release, plan, run = _approved_context(tmp_path, targets=targets)

    def dispatch(request):
        request.progress_recorder(
            request,
            ("shopee:global_master:update",),
            "global_master_confirmed",
            {"verified": True},
        )
        raise RuntimeError("later regional invocation is ambiguous")

    registry = _registry(targets, dispatch_override=dispatch)
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan, run=run, product_revision=31, registry=registry
    )
    worker = OneClickReleaseWorker(
        control, lambda: registry, dispatch_enabled=lambda: True
    )
    worker.advance_once(job["job_id"])
    worker.advance_once(job["job_id"])
    target = control.get_job(job_id=job["job_id"])["targets"][0]
    assert target["result"]["external_write_count"] is None
    assert target["result"]["external_write_classes"] == [
        "shopee:global_master:update",
        "UNKNOWN",
    ]
    pending = control.pending_outcome_receipts()
    assert pending[0]["receipt"]["dispatch"]["external_write_count"] is None
    assert pending[0]["receipt"]["dispatch"][
        "external_write_classes"
    ] == ["shopee:global_master:update", "UNKNOWN"]


@pytest.mark.parametrize("with_write", [True, False])
def test_success_receipt_distinguishes_submission_from_existing_no_write(
    tmp_path,
    with_write,
):
    targets = ["shopee:PH"]
    release, plan, run = _approved_context(tmp_path, targets=targets)

    def dispatch(_request):
        return DispatchTargetResult(
            canonical_status=SUCCEEDED,
            reason_category="CAPABILITY",
            reason_scope="TARGET",
            reason_code="official_readback_exact",
            reason_detail="official readback is exact",
            external_writes=(
                ("shopee:regional_publish",) if with_write else ()
            ),
            external_id="internal-existing-id",
            submission_accepted=with_write,
            readback_verified=True,
        )

    registry = _registry(targets, dispatch_override=dispatch)
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan, run=run, product_revision=31, registry=registry
    )
    worker = OneClickReleaseWorker(
        control, lambda: registry, dispatch_enabled=lambda: True
    )
    worker.advance_once(job["job_id"])
    worker.advance_once(job["job_id"])
    receipt = control.pending_outcome_receipts()[0]["receipt"]
    assert receipt["outcome"]["class"] == "SUCCESS"
    assert receipt["dispatch"]["boundary"] == (
        "ACCEPTED" if with_write else "NOT_REACHED"
    )
    assert receipt["duplicate_prevented"] is (not with_write)
    readback = release.get_run(run["run_id"])["targets"][0]["readback"]
    assert readback["evidence"]["submission_accepted"] is with_write


def test_submitted_unverified_outcome_is_truthful_05_shape(tmp_path):
    targets = ["tiktok:MX", "miaoshou:COMMON"]
    # Put COMMON first to keep the immutable dependency valid.
    targets = ["miaoshou:COMMON", "tiktok:MX"]
    release, plan, run = _approved_context(tmp_path, targets=targets)
    registry = _registry(targets, manual_labels=("tiktok:MX",))
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan, run=run, product_revision=31, registry=registry
    )
    control.prepare_job(job["job_id"], registry)
    common = control.claim_next_dispatch(job["job_id"], registry)
    control.record_dispatch_result(
        common,
        DispatchTargetResult(
            canonical_status=SUCCEEDED,
            reason_category="CAPABILITY",
            reason_scope="TARGET",
            reason_code="common_exact",
            reason_detail="COMMON exact",
            external_writes=(),
            external_id="common-internal",
            readback_verified=True,
        ),
    )
    request = control.claim_next_dispatch(job["job_id"], registry)
    control.record_dispatch_result(
        request,
        DispatchTargetResult(
            canonical_status=SUBMITTED_UNVERIFIED,
            reason_category="POST_WRITE",
            reason_scope="TARGET",
            reason_code="platform_submission_accepted",
            reason_detail="platform submission accepted",
            external_writes=("miaoshou:tiktok_publish",),
            external_id="tiktok-internal",
            submission_accepted=True,
        ),
    )
    receipts = {
        row["target_label"]: row["receipt"]
        for row in control.pending_outcome_receipts()
    }
    submitted = receipts["tiktok:MX"]
    assert submitted["outcome"]["class"] == "SUBMITTED_UNVERIFIED"
    assert submitted["manual"]["status"] == "PENDING"
    assert submitted["reconciliation"]["status"] == "NOT_REQUIRED"


def test_dispatch_disabled_is_durable_block_not_ready_spin(tmp_path):
    targets = ["shopee:PH"]
    release, plan, run = _approved_context(tmp_path, targets=targets)
    dispatch_calls = []
    registry = _registry(targets, dispatch_calls=dispatch_calls)
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan, run=run, product_revision=31, registry=registry
    )
    worker = OneClickReleaseWorker(
        control, lambda: registry, dispatch_enabled=lambda: False
    )
    assert worker.advance_once(job["job_id"]) is True
    projected = control.get_job(job_id=job["job_id"])
    assert projected["phase"] == "BLOCKED"
    assert projected["runnable_target_count"] == 0
    assert projected["targets"][0]["next_action"] == (
        "enable_oneclick_dispatch"
    )
    assert worker.advance_once(job["job_id"]) is False
    assert dispatch_calls == []
    assert release.get_run(run["run_id"])["targets"][0]["attempts"] == 0


def test_raw_adapter_details_never_reach_sqlite_or_public_projection(tmp_path):
    targets = ["shopee:PH"]
    release, plan, run = _approved_context(tmp_path, targets=targets)
    secret = (
        "token=RAW_SECRET_93 https://merchant.example/raw "
        "title=SECRET_TITLE description=SECRET_DESCRIPTION"
    )

    def prepare(_request):
        raise RuntimeError(secret)

    registry = _registry(targets, prepare_override=prepare)
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan, run=run, product_revision=31, registry=registry
    )
    public = control.prepare_job(job["job_id"], registry)
    assert "RAW_SECRET_93" not in json.dumps(public)
    assert b"RAW_SECRET_93" not in release.path.read_bytes()
    assert b"merchant.example" not in release.path.read_bytes()


def test_raw_dispatch_exception_is_digest_only_in_durable_rows(tmp_path):
    targets = ["shopee:PH"]
    release, plan, run = _approved_context(tmp_path, targets=targets)
    secret = (
        "token=RAW_DISPATCH_SECRET https://merchant.example/response "
        "raw_response=SECRET_BODY"
    )

    def dispatch(_request):
        raise RuntimeError(secret)

    registry = _registry(targets, dispatch_override=dispatch)
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan, run=run, product_revision=31, registry=registry
    )
    worker = OneClickReleaseWorker(
        control, lambda: registry, dispatch_enabled=lambda: True
    )
    worker.advance_once(job["job_id"])
    worker.advance_once(job["job_id"])
    public = control.get_job(job_id=job["job_id"])
    encoded = json.dumps(public)
    database_bytes = release.path.read_bytes()
    assert "RAW_DISPATCH_SECRET" not in encoded
    assert b"RAW_DISPATCH_SECRET" not in database_bytes
    assert b"merchant.example" not in database_bytes
    pending = control.pending_outcome_receipts()[0]
    assert "RAW_DISPATCH_SECRET" not in json.dumps(pending["receipt"])


def test_zero_write_retry_appends_attempt_outcome_then_succeeds(tmp_path):
    targets = ["shopee:PH"]
    release, plan, run = _approved_context(tmp_path, targets=targets)
    dispatch_calls = []

    def dispatch(request):
        dispatch_calls.append(request.target_label)
        if len(dispatch_calls) == 1:
            raise PreDispatchInvocationError("known pre-submit credential gap")
        return DispatchTargetResult(
            canonical_status=SUCCEEDED,
            reason_category="CAPABILITY",
            reason_scope="TARGET",
            reason_code="official_readback_exact",
            reason_detail="official readback is exact",
            external_writes=("shopee:regional_publish",),
            external_id="internal-success-id",
            submission_accepted=True,
            readback_verified=True,
        )

    registry = _registry(targets, dispatch_override=dispatch)
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan, run=run, product_revision=31, registry=registry
    )
    worker = OneClickReleaseWorker(
        control, lambda: registry, dispatch_enabled=lambda: True
    )
    worker.advance_once(job["job_id"])
    worker.advance_once(job["job_id"])
    assert control.get_job(job_id=job["job_id"])["targets"][0][
        "status"
    ] == FAILED_PRE_SUBMIT
    assert control.resume_exact_zero_write_failures(job["job_id"]) == 1
    worker.advance_once(job["job_id"])
    worker.advance_once(job["job_id"])

    projected = control.get_job(job_id=job["job_id"])
    assert projected["targets"][0]["status"] == SUCCEEDED
    assert release.get_run(run["run_id"])["targets"][0]["attempts"] == 2
    assert dispatch_calls == ["shopee:PH", "shopee:PH"]
    outcomes = control.pending_outcome_receipts()
    assert [row["attempt"] for row in outcomes] == [1, 2]
    assert [row["receipt"]["outcome"]["class"] for row in outcomes] == [
        "FAILURE",
        "SUCCESS",
    ]


@pytest.mark.parametrize("category", ["CONTENT", "LOGISTICS"])
def test_outcome_receipt_preserves_05_failure_category(tmp_path, category):
    targets = ["shopee:PH"]
    release, plan, run = _approved_context(tmp_path, targets=targets)
    registry = _registry(targets)
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan, run=run, product_revision=31, registry=registry
    )
    control.prepare_job(job["job_id"], registry)
    request = control.claim_next_dispatch(job["job_id"], registry)
    control.record_dispatch_result(
        request,
        DispatchTargetResult(
            canonical_status=FAILED_PRE_SUBMIT,
            reason_category=category,
            reason_scope="TARGET",
            reason_code=f"{category.casefold()}_contract_failed",
            reason_detail=f"{category} contract failed before dispatch",
            external_writes=(),
        ),
    )
    pending = control.pending_outcome_receipts()[0]
    receipt = pending["receipt"]
    assert receipt["error"]["category"] == category
    try:
        from domains.data_operations.release_outcomes import (
            adapt_release_outcome_receipt,
        )
    except ImportError:
        # The exact base intentionally predates the integrated 05 module.
        # These assertions mirror its release-outcome-receipt/v1 boundary;
        # once 05 is present this same test exercises the authoritative adapter.
        assert receipt["schema_version"] == "release-outcome-receipt/v1"
        assert receipt["outcome"]["class"] == "FAILURE"
    else:
        fact = adapt_release_outcome_receipt(receipt)
        assert fact.error_category == category
        assert fact.outcome_class == "FAILURE"


def test_outcome_consumer_is_idempotent_metadata_only(tmp_path):
    targets = ["shopee:PH"]
    release, plan, run = _approved_context(tmp_path, targets=targets)
    registry = _registry(targets)
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan, run=run, product_revision=31, registry=registry
    )
    worker = OneClickReleaseWorker(
        control, lambda: registry, dispatch_enabled=lambda: True
    )
    worker.advance_once(job["job_id"])
    worker.advance_once(job["job_id"])
    pending = control.pending_outcome_receipts()[0]
    control.record_outcome_consumer_result(
        job_id=pending["job_id"],
        target_label=pending["target_label"],
        attempt=pending["attempt"],
        receipt_digest=pending["receipt_digest"],
        fact_digest="f" * 64,
        error_code=None,
    )
    assert control.pending_outcome_receipts() == []
    projected = control.get_job(job_id=job["job_id"])
    assert projected["targets"][0]["status"] == SUCCEEDED
    assert projected["targets"][0]["outcome_receipt"][
        "consumer_status"
    ] == "SUCCEEDED"
    assert projected["targets"][0]["outcome_receipt"]["fact_digest"] == (
        "f" * 64
    )
