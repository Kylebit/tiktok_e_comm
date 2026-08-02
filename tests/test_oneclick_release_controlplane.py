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
    SUCCEEDED_MANUAL_REVIEW,
    SHARED_RESOURCE_SCHEMA,
    SHOPEE_GLOBAL_MODEL_WRITE,
    SHOPEE_GLOBAL_MASTER_POLICY,
    SHOPEE_GLOBAL_TARGET,
    SHOPEE_GLOBAL_WRITE,
    SHOPEE_GLOBAL_WRITE_CLASSES,
    SHOPEE_IMAGE_UPLOAD_WRITE,
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
    shopee_shared_resource_owner_key,
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
    from shared_platform.shopee_global_plan import (
        approve_shopee_global_plan,
        build_shopee_global_plan_candidate,
        serialize_approved_shopee_global_plan,
    )
    from tests.test_shopee_global_plan import _base_args

    global_args = _base_args()
    global_args["ordered_approved_images"] = [
        {
            "source_url": f"https://img.example/{position}.jpg",
            "source_image_digest": f"{position}" * 64,
        }
        for position in range(1, 7)
    ]
    from shared_platform.target_scoped_release_contracts import (
        approved_source_image_manifest_digest,
    )
    global_args["approved_source_image_manifest_digest"] = (
        approved_source_image_manifest_digest(
            [
                row["source_url"]
                for row in global_args["ordered_approved_images"]
            ]
        )
    )
    global_args["selected_image_positions"] = list(range(1, 7))
    global_candidate = build_shopee_global_plan_candidate(**global_args)
    global_approval = approve_shopee_global_plan(
        global_candidate,
        approved_by="Kyle",
        confirm_approved_shopee_global_plan=True,
        expected_candidate_digest=global_candidate.candidate_digest,
    )
    global_record = serialize_approved_shopee_global_plan(global_approval)
    global_raw = global_approval._plan.payload()
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
        "images": [
            {
                "position": position,
                "image_url": f"https://img.example/{position}.jpg",
            }
            for position in range(1, 7)
        ],
        "approved_shopee_global_plan": {
            "schema_version": "approved-shopee-global-plan/v1",
            "mode": global_approval.mode,
            "candidate_digest": global_approval.candidate_digest,
            "approved_plan_digest": global_approval.approved_plan_digest,
            "selected_image_positions": [1, 2, 3, 4, 5, 6],
            "selected_source_image_manifest_digest": global_raw[
                "selected_source_image_manifest_digest"
            ],
            "record_digest": hashlib.sha256(
                global_record.encode("utf-8")
            ).hexdigest(),
        },
        "_approved_shopee_global_plan_record": global_record,
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
    global_prepare_override=None,
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
    if "shopee_cnsc_publish" in by_adapter:
        by_adapter["shopee_cnsc_publish"].insert(0, SHOPEE_GLOBAL_TARGET)

    result = {}
    for adapter_name, labels in by_adapter.items():
        def prepare(request, _labels=tuple(labels)):
            if request.target_label == SHOPEE_GLOBAL_TARGET:
                if global_prepare_override:
                    return global_prepare_override(request)
                master_lineage = _digest("fixture-approved-shopee-master")
                return PrepareTargetResult(
                    classification=EXACT_READY_AUTOMATIC,
                    reason_category="CAPABILITY",
                    reason_scope="TARGET",
                    reason_code="official_global_existing_exact",
                    reason_detail="official global master is exact",
                    command={
                        "kind": "EXISTING_GLOBAL",
                        "target": SHOPEE_GLOBAL_TARGET,
                    },
                    proof={
                        "kind": "existing-global-proof",
                        "target": SHOPEE_GLOBAL_TARGET,
                    },
                    shared_resource={
                        "schema_version": SHARED_RESOURCE_SCHEMA,
                        "policy_version": SHOPEE_GLOBAL_MASTER_POLICY,
                        "mode": "EXISTING_GLOBAL",
                        "owner_key": shopee_shared_resource_owner_key(
                            request,
                            master_lineage,
                        ),
                        "master_lineage_digest": master_lineage,
                        "approved_selected_image_count": 6,
                        "expected_external_write_count": 0,
                        "global_identity_digest": _digest(
                            "fixture-global-item"
                        ),
                        "master_evidence_digest": _digest(
                            "fixture-global-master-evidence"
                        ),
                    },
                )
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


def _mvp_miaoshou_registry(
    targets,
    *,
    prepare_calls=None,
    dispatch_calls=None,
    prepare_override=None,
    dispatch_override=None,
):
    """One Miaoshou API family owns every storefront in the MVP."""

    prepare_calls = prepare_calls if prepare_calls is not None else []
    dispatch_calls = dispatch_calls if dispatch_calls is not None else []

    def prepare(request):
        prepare_calls.append(request.target_label)
        if prepare_override:
            return prepare_override(request)
        return PrepareTargetResult(
            classification=EXACT_READY_AUTOMATIC,
            reason_category="CAPABILITY",
            reason_scope="TARGET",
            reason_code="miaoshou_command_ready",
            reason_detail="Miaoshou target command is ready",
            command={
                "kind": "miaoshou-direct-store",
                "target": request.target_label,
            },
            proof={
                "kind": "approved-plan-only",
                "target": request.target_label,
            },
        )

    def dispatch(request):
        dispatch_calls.append(request.target_label)
        if dispatch_override:
            return dispatch_override(request)
        return DispatchTargetResult(
            canonical_status=SUBMITTED_UNVERIFIED,
            reason_category="POST_WRITE",
            reason_scope="TARGET",
            reason_code="miaoshou_submission_accepted",
            reason_detail="Miaoshou accepted the target submission",
            external_writes=("miaoshou:storefront:submit",),
            external_write_count=1,
            confirmed_external_write_count_lower_bound=1,
            possible_external_write_count_upper_bound=1,
            external_id=f"miaoshou-{request.target_label}",
            submission_accepted=True,
            readback_verified=False,
            evidence={"checks": {"miaoshou_submission_accepted": True}},
        )

    registry = {
        "miaoshou-direct-store/v1": AdapterRegistration(
            adapter_name="miaoshou-direct-store/v1",
            target_labels=tuple(
                label for label in targets if label != SHOPEE_GLOBAL_TARGET
            ),
            prepare=prepare,
            dispatch=dispatch,
            policy_digest=_digest("miaoshou-direct-store/v1"),
            prepare_is_read_only=True,
            consumes_prepared_command=True,
            preserves_idempotency_key=True,
            reports_truthful_receipt=True,
        )
    }
    if SHOPEE_GLOBAL_TARGET in targets:
        registry["shopee_cnsc_publish"] = AdapterRegistration(
            adapter_name="shopee_cnsc_publish",
            target_labels=(SHOPEE_GLOBAL_TARGET,),
            prepare=prepare,
            dispatch=dispatch,
            policy_digest=_digest("shopee_cnsc_publish"),
            prepare_is_read_only=True,
            consumes_prepared_command=True,
            preserves_idempotency_key=True,
            reports_truthful_receipt=True,
        )
    return registry


def _ensure_new_global_prepare(request):
    master_lineage = _digest("fixture-approved-new-shopee-master")
    return PrepareTargetResult(
        classification=EXACT_READY_AUTOMATIC,
        reason_category="CAPABILITY",
        reason_scope="TARGET",
        reason_code="official_global_absent_create_required",
        reason_detail="one approved global master must be created",
        command={"kind": "ENSURE_NEW_GLOBAL"},
        proof={"kind": "global-absence-proof"},
        shared_resource={
            "schema_version": SHARED_RESOURCE_SCHEMA,
            "policy_version": SHOPEE_GLOBAL_MASTER_POLICY,
            "mode": "ENSURE_NEW",
            "owner_key": shopee_shared_resource_owner_key(
                request,
                master_lineage,
            ),
            "master_lineage_digest": master_lineage,
            "approved_selected_image_count": 6,
            "expected_external_write_count": 8,
        },
    )


def _verified_global_result(request, *, image_count=6):
    declaration = request.shared_resource_context
    identity_digest = _digest("created-global-identity")
    return DispatchTargetResult(
        canonical_status=SUCCEEDED,
        reason_category="POST_WRITE",
        reason_scope="TARGET",
        reason_code="global_master_created_and_verified",
        reason_detail="global master and model readback are exact",
        external_writes=SHOPEE_GLOBAL_WRITE_CLASSES,
        external_write_count=image_count + 2,
        confirmed_external_write_count_lower_bound=image_count + 2,
        possible_external_write_count_upper_bound=image_count + 2,
        external_id="sha256:" + identity_digest,
        submission_accepted=True,
        readback_verified=True,
        evidence={
            "shared_resource": {
                "schema_version": SHARED_RESOURCE_SCHEMA,
                "policy_version": SHOPEE_GLOBAL_MASTER_POLICY,
                "mode": "ENSURE_NEW",
                "owner_key": declaration["owner_key"],
                "master_lineage_digest": declaration[
                    "master_lineage_digest"
                ],
                "global_identity_digest": identity_digest,
                "master_evidence_digest": _digest(
                    "created-global-master-readback"
                ),
            }
        },
    )


def test_mvp_preview_is_startable_without_prepare_calls_or_prerequisites(
    tmp_path,
):
    targets = ["tiktok:MX", "shopee:MY", "ozon:RU"]
    _release, plan, run = _approved_context(
        tmp_path,
        targets=targets,
        inventory_ready=False,
    )
    prepare_calls = []
    registry = _mvp_miaoshou_registry(
        targets,
        prepare_calls=prepare_calls,
    )

    preview = build_batch_preview(
        plan=plan,
        run=run,
        product_revision=31,
        registry=registry,
    )

    assert prepare_calls == []
    assert preview["start_allowed"] is True
    assert preview["blocked"] == []
    assert [row["target_label"] for row in preview["targets"]] == targets
    assert preview["shared_controls"] == []
    assert all(
        row["dependency"]["state"] == "SATISFIED"
        and row["runnable_now"] is False
        for row in preview["targets"]
    )


def test_mvp_execution_replaces_shopee_regions_with_global_owner_only(
    tmp_path,
):
    targets = [
        "tiktok:MX",
        "shopee:MY",
        "shopee:PH",
        "ozon:RU",
    ]
    _release, plan, run = _approved_context(
        tmp_path,
        targets=targets,
        inventory_ready=False,
    )
    registry = _mvp_miaoshou_registry(
        [*targets, SHOPEE_GLOBAL_TARGET],
    )

    preview = build_batch_preview(
        plan=plan,
        run=run,
        product_revision=31,
        registry=registry,
    )

    labels = [row["target_label"] for row in preview["targets"]]
    assert labels == ["tiktok:MX", "ozon:RU"]
    assert [
        row["target_label"] for row in preview["shared_controls"]
    ] == [SHOPEE_GLOBAL_TARGET]
    assert "shopee:MY" not in labels
    assert "shopee:PH" not in labels


def test_explicit_batch_can_activate_only_one_platform_scope(tmp_path):
    targets = ["tiktok:MX", "shopee:MY", "ozon:RU"]
    release, plan, run = _approved_context(
        tmp_path,
        targets=targets,
        inventory_ready=False,
    )
    registry = _mvp_miaoshou_registry(
        [*targets, SHOPEE_GLOBAL_TARGET],
    )
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan,
        run=run,
        product_revision=31,
        registry=registry,
    )

    started = control.start_explicit_batch(
        job["job_id"],
        target_labels=(SHOPEE_GLOBAL_TARGET,),
    )

    assert started["batch_scope_targets"] == [SHOPEE_GLOBAL_TARGET]
    assert started["targets"] == []
    assert [row["target_label"] for row in started["shared_controls"]] == [
        SHOPEE_GLOBAL_TARGET
    ]


@pytest.mark.parametrize(
    "scope_label",
    ["tiktok:MX", SHOPEE_GLOBAL_TARGET, "ozon:RU"],
)
def test_explicit_platform_scope_prepares_and_claims_only_itself(
    tmp_path,
    scope_label,
):
    targets = ["tiktok:MX", "shopee:MY", "ozon:RU"]
    release, plan, run = _approved_context(
        tmp_path,
        targets=targets,
        inventory_ready=False,
    )
    prepare_calls = []

    def prepare(request):
        prepare_calls.append(request.target_label)
        if request.target_label == SHOPEE_GLOBAL_TARGET:
            return _ensure_new_global_prepare(request)
        return PrepareTargetResult(
            classification=EXACT_READY_AUTOMATIC,
            reason_category="CAPABILITY",
            reason_scope="TARGET",
            reason_code="platform_command_ready",
            reason_detail="the isolated platform command is ready",
            command={"kind": "isolated-platform", "target": request.target_label},
            proof={"kind": "approved-plan-only", "target": request.target_label},
        )

    registry = _mvp_miaoshou_registry(
        [*targets, SHOPEE_GLOBAL_TARGET],
        prepare_override=prepare,
    )
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan,
        run=run,
        product_revision=31,
        registry=registry,
    )
    started = control.start_explicit_batch(
        job["job_id"],
        target_labels=(scope_label,),
    )

    assert started["batch_scope_targets"] == [scope_label]
    prepared = control.prepare_job(job["job_id"], registry)
    request = control.claim_next_dispatch(job["job_id"], registry)

    assert prepare_calls == [scope_label]
    assert prepared["batch_scope_targets"] == [scope_label]
    assert request is not None
    assert request.target_label == scope_label


def test_mvp_explicit_click_starts_new_batch_after_partial_reconciliation(
    tmp_path,
):
    targets = ["tiktok:MX", "shopee:MY", "ozon:RU"]
    release, plan, run = _approved_context(
        tmp_path,
        targets=targets,
        inventory_ready=False,
    )
    dispatch_calls = []
    first_batch = True

    def dispatch(request):
        nonlocal first_batch
        if first_batch and request.target_label == "tiktok:MX":
            return DispatchTargetResult(
                canonical_status=RECONCILIATION_REQUIRED,
                reason_category="POST_WRITE",
                reason_scope="TARGET",
                reason_code="miaoshou_submission_unknown",
                reason_detail="Miaoshou submission outcome is unknown",
                external_writes=("miaoshou:storefront:submit",),
                external_write_count=None,
                confirmed_external_write_count_lower_bound=0,
                possible_external_write_count_upper_bound=1,
                dispatch_outcome_unknown=True,
                evidence={"checks": {"outcome_unknown": True}},
            )
        return DispatchTargetResult(
            canonical_status=SUBMITTED_UNVERIFIED,
            reason_category="POST_WRITE",
            reason_scope="TARGET",
            reason_code="miaoshou_submission_accepted",
            reason_detail="Miaoshou accepted the target submission",
            external_writes=("miaoshou:storefront:submit",),
            external_write_count=1,
            confirmed_external_write_count_lower_bound=1,
            possible_external_write_count_upper_bound=1,
            external_id=f"miaoshou-{request.target_label}",
            submission_accepted=True,
            readback_verified=False,
            evidence={"checks": {"miaoshou_submission_accepted": True}},
        )

    registry = _mvp_miaoshou_registry(
        targets,
        dispatch_calls=dispatch_calls,
        dispatch_override=dispatch,
    )
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan,
        run=run,
        product_revision=31,
        registry=registry,
    )
    control.start_explicit_batch(job["job_id"])
    worker = OneClickReleaseWorker(
        control,
        lambda: registry,
        dispatch_enabled=lambda: True,
    )
    while worker.advance_once(job["job_id"]):
        pass

    first = control.get_job(job_id=job["job_id"])
    assert first["phase"] == "BLOCKED"
    assert dispatch_calls == targets
    assert release.get_run(run["run_id"])["status"] == "PARTIAL_FAILED"

    first_batch = False
    restarted = control.start_explicit_batch(job["job_id"])
    assert restarted["phase"] == "PENDING"
    assert restarted["batch_sequence"] == 2
    assert all(row["status"] == "PENDING" for row in restarted["targets"])

    while worker.advance_once(job["job_id"]):
        pass

    second = control.get_job(job_id=job["job_id"])
    assert dispatch_calls == targets + targets
    assert second["phase"] == "WAITING_MANUAL_ACCEPTANCE"
    assert [row["dispatch_count"] for row in second["targets"]] == [2, 2, 2]
    assert all(row["status"] == SUBMITTED_UNVERIFIED for row in second["targets"])
    assert len(control.pending_outcome_receipts()) == 6


def test_mvp_target_failure_does_not_prevent_later_storefront_dispatch(
    tmp_path,
):
    targets = ["tiktok:GB", "shopee:VN", "ozon:RU"]
    release, plan, run = _approved_context(
        tmp_path,
        targets=targets,
        inventory_ready=False,
    )
    calls = []

    def dispatch(request):
        if request.target_label == "tiktok:GB":
            raise PreDispatchInvocationError("fixture pre-submit failure")
        return DispatchTargetResult(
            canonical_status=SUBMITTED_UNVERIFIED,
            reason_category="POST_WRITE",
            reason_scope="TARGET",
            reason_code="miaoshou_submission_accepted",
            reason_detail="Miaoshou accepted the target submission",
            external_writes=("miaoshou:storefront:submit",),
            external_write_count=1,
            confirmed_external_write_count_lower_bound=1,
            possible_external_write_count_upper_bound=1,
            external_id=f"miaoshou-{request.target_label}",
            submission_accepted=True,
            readback_verified=False,
            evidence={"checks": {"miaoshou_submission_accepted": True}},
        )

    registry = _mvp_miaoshou_registry(
        targets,
        dispatch_calls=calls,
        dispatch_override=dispatch,
    )
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan,
        run=run,
        product_revision=31,
        registry=registry,
    )
    control.start_explicit_batch(job["job_id"])
    worker = OneClickReleaseWorker(
        control,
        lambda: registry,
        dispatch_enabled=lambda: True,
    )
    while worker.advance_once(job["job_id"]):
        pass

    projected = control.get_job(job_id=job["job_id"])
    assert calls == targets
    by_target = {row["target_label"]: row for row in projected["targets"]}
    assert by_target["tiktok:GB"]["status"] == FAILED_PRE_SUBMIT
    assert by_target["shopee:VN"]["status"] == SUBMITTED_UNVERIFIED
    assert by_target["ozon:RU"]["status"] == SUBMITTED_UNVERIFIED


def test_mvp_explicit_click_counts_legacy_terminal_job_as_prior_batch(
    tmp_path,
):
    targets = [
        "miaoshou:COMMON",
        "tiktok:MX",
        "shopee:MY",
        "ozon:RU",
    ]
    release, plan, run = _approved_context(
        tmp_path,
        targets=targets,
        inventory_ready=True,
    )
    legacy_registry = _registry(targets)
    control = OneClickReleaseStore(release.path)
    legacy = control.ensure_job(
        plan=plan,
        run=run,
        product_revision=31,
        registry=legacy_registry,
    )
    with sqlite3.connect(release.path) as connection:
        connection.execute(
            """
            UPDATE oneclick_release_jobs
            SET status = 'BLOCKED'
            WHERE job_id = ?
            """,
            (legacy["job_id"],),
        )
        connection.execute(
            """
            UPDATE oneclick_release_targets
            SET status = CASE
                WHEN target_label = 'miaoshou:COMMON' THEN 'SUCCEEDED'
                WHEN target_label = 'tiktok:MX' THEN 'RECONCILIATION_REQUIRED'
                ELSE 'BLOCKED_CAPABILITY'
            END
            WHERE job_id = ?
            """,
            (legacy["job_id"],),
        )
        connection.execute(
            """
            UPDATE release_target_runs
            SET status = CASE
                WHEN target_label = 'miaoshou:COMMON' THEN 'SUCCEEDED'
                ELSE 'FAILED'
            END,
            attempts = 1
            WHERE run_id = ?
            """,
            (run["run_id"],),
        )
        connection.execute(
            "UPDATE release_runs SET status = 'PARTIAL_FAILED' WHERE run_id = ?",
            (run["run_id"],),
        )

    mvp_targets = ["tiktok:MX", "shopee:MY", "ozon:RU"]
    mvp_registry = _mvp_miaoshou_registry(mvp_targets)
    rebound = control.ensure_job(
        plan=release.get_plan(plan["plan_id"]),
        run=release.get_run(run["run_id"]),
        product_revision=31,
        registry=mvp_registry,
    )
    assert rebound["job_id"] == legacy["job_id"]
    assert rebound["shared_controls"] == []
    assert [row["target_label"] for row in rebound["targets"]] == mvp_targets
    assert rebound["batch_sequence"] == 1

    started = control.start_explicit_batch(rebound["job_id"])
    assert started["batch_sequence"] == 2
    assert started["phase"] == "PENDING"
    assert all(row["status"] == "PENDING" for row in started["targets"])
    assert all(
        row["dependency"]["state"] == "SATISFIED"
        for row in started["targets"]
    )


@pytest.mark.parametrize(
    ("blocked_target", "classification", "category"),
    [
        ("tiktok:MX", BLOCKED_CAPABILITY, "CONTENT"),
        ("shopee:MY", BLOCKED_CAPABILITY, "CAPABILITY"),
        ("ozon:RU", BLOCKED_INVENTORY, "INVENTORY"),
    ],
)
def test_mvp_prepare_blocker_is_target_local_and_other_targets_still_run(
    tmp_path,
    blocked_target,
    classification,
    category,
):
    targets = ["tiktok:MX", "shopee:MY", "ozon:RU"]
    release, plan, run = _approved_context(
        tmp_path,
        targets=targets,
        inventory_ready=False,
    )
    dispatch_calls = []

    def prepare(request):
        if request.target_label == blocked_target:
            return PrepareTargetResult(
                classification=classification,
                reason_category=category,
                reason_scope="TARGET",
                reason_code="fixture_target_local_blocker",
                reason_detail="fixture target-local preparation blocker",
            )
        return PrepareTargetResult(
            classification=EXACT_READY_AUTOMATIC,
            reason_category="CAPABILITY",
            reason_scope="TARGET",
            reason_code="miaoshou_command_ready",
            reason_detail="Miaoshou target command is ready",
            command={"target": request.target_label},
            proof={"target": request.target_label},
        )

    registry = _mvp_miaoshou_registry(
        targets,
        prepare_override=prepare,
        dispatch_calls=dispatch_calls,
    )
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan,
        run=run,
        product_revision=31,
        registry=registry,
    )
    control.start_explicit_batch(job["job_id"])
    worker = OneClickReleaseWorker(
        control,
        lambda: registry,
        dispatch_enabled=lambda: True,
    )
    while worker.advance_once(job["job_id"]):
        pass

    assert dispatch_calls == [
        label for label in targets if label != blocked_target
    ]
    projected = control.get_job(job_id=job["job_id"])
    by_target = {row["target_label"]: row for row in projected["targets"]}
    assert by_target[blocked_target]["status"] == classification
    assert all(
        by_target[label]["status"] == SUBMITTED_UNVERIFIED
        for label in targets
        if label != blocked_target
    )


def _record_confirmed_global_writes(request, *, image_count=6):
    classes = (SHOPEE_IMAGE_UPLOAD_WRITE,)
    for count in range(1, image_count + 1):
        request.progress_recorder(
            request,
            classes,
            f"image_upload-{count}",
            {"count": count},
            None,
            count - 1,
            count,
            "PRE_INVOCATION_INTENT",
        )
        request.progress_recorder(
            request,
            classes,
            f"image_upload-{count}",
            {"count": count},
            count,
            count,
            count,
            "POST_RESPONSE_CONFIRMED",
        )
    count = image_count + 1
    classes = (SHOPEE_IMAGE_UPLOAD_WRITE, SHOPEE_GLOBAL_WRITE)
    request.progress_recorder(
        request,
        classes,
        "global_create-1",
        {"count": count},
        None,
        count - 1,
        count,
        "PRE_INVOCATION_INTENT",
    )
    request.progress_recorder(
        request,
        classes,
        "global_create-1",
        {"count": count},
        count,
        count,
        count,
        "POST_RESPONSE_CONFIRMED",
    )
    count += 1
    request.progress_recorder(
        request,
        SHOPEE_GLOBAL_WRITE_CLASSES,
        "model_init-1",
        {"count": count},
        None,
        count - 1,
        count,
        "PRE_INVOCATION_INTENT",
    )
    request.progress_recorder(
        request,
        SHOPEE_GLOBAL_WRITE_CLASSES,
        "model_init-1",
        {"count": count},
        count,
        count,
        count,
        "POST_RESPONSE_CONFIRMED",
    )


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


def test_shopee_global_uses_explicit_approved_image_selection_not_all_images(
    tmp_path,
):
    payload = _plan_payload(targets=["shopee:MY"])
    payload["images"].extend(
        {
            "position": position,
            "image_url": f"https://img.example/{position}.jpg",
        }
        for position in range(7, 15)
    )
    release = ReleaseStore(tmp_path / "release.db")
    created = release.create_plan(payload)
    release.approve_plan(
        created["plan_id"],
        approved_by="Kyle",
        user_approved=True,
        confirmation_token=created["confirmation_token"],
    )
    plan = release.get_plan(created["plan_id"])
    run = release.start_run(created["plan_id"])
    registry = _registry(
        ["shopee:MY"],
        global_prepare_override=_ensure_new_global_prepare,
    )
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan, run=run, product_revision=31, registry=registry
    )
    prepared = control.prepare_job(job["job_id"], registry)
    assert prepared["shared_controls"][0]["status"] == "READY"
    request = control.claim_next_dispatch(job["job_id"], registry)
    declaration = request.shared_resource_context
    assert declaration["approved_selected_image_count"] == 6
    assert declaration["expected_external_write_count"] == 8
    assert len(payload["images"]) == 14

    missing_payload = _plan_payload(targets=["shopee:MY"])
    missing_payload.pop("approved_shopee_global_plan")
    release2 = ReleaseStore(tmp_path / "missing.db")
    created2 = release2.create_plan(missing_payload)
    release2.approve_plan(
        created2["plan_id"],
        approved_by="Kyle",
        user_approved=True,
        confirmation_token=created2["confirmation_token"],
    )
    plan2 = release2.get_plan(created2["plan_id"])
    run2 = release2.start_run(created2["plan_id"])
    control2 = OneClickReleaseStore(release2.path)
    job2 = control2.ensure_job(
        plan=plan2, run=run2, product_revision=31, registry=registry
    )
    blocked = control2.prepare_job(job2["job_id"], registry)
    assert blocked["shared_controls"][0]["status"] == BLOCKED_CAPABILITY
    assert blocked["shared_controls"][0]["reason"][
        "category"
    ] == "SYSTEMIC_CONTRACT"
    assert control2.claim_next_dispatch(job2["job_id"], registry) is None
    assert release2.get_run(run2["run_id"])["targets"][0][
        "attempts"
    ] == 0


def test_missing_shopee_global_approval_does_not_block_other_channels(
    tmp_path,
):
    targets = ["miaoshou:COMMON", "tiktok:MX", "shopee:MY"]
    payload = _plan_payload(targets=targets)
    payload.pop("approved_shopee_global_plan")
    payload.pop("_approved_shopee_global_plan_record")
    release = ReleaseStore(tmp_path / "release.db")
    created = release.create_plan(payload)
    release.approve_plan(
        created["plan_id"],
        approved_by="Kyle",
        user_approved=True,
        confirmation_token=created["confirmation_token"],
    )
    plan = release.get_plan(created["plan_id"])
    run = release.start_run(created["plan_id"])
    registry = _registry(targets)
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan,
        run=run,
        product_revision=31,
        registry=registry,
    )

    projected = control.prepare_job(job["job_id"], registry)

    shared = projected["shared_controls"][0]
    assert shared["target_label"] == SHOPEE_GLOBAL_TARGET
    assert shared["status"] == BLOCKED_CAPABILITY
    assert projected["phase"] == "READY"
    common = next(
        row
        for row in projected["targets"]
        if row["target_label"] == "miaoshou:COMMON"
    )
    shopee = next(
        row
        for row in projected["targets"]
        if row["target_label"] == "shopee:MY"
    )
    assert common["runnable_now"] is True
    assert shopee["runnable_now"] is False
    common_request = control.claim_next_dispatch(job["job_id"], registry)
    assert common_request is not None
    assert common_request.target_label == "miaoshou:COMMON"
    common_result = registry[
        "new_product_workbench_miaoshou_commit"
    ].dispatch(common_request)
    control.record_dispatch_result(common_request, common_result)
    tiktok_request = control.claim_next_dispatch(job["job_id"], registry)
    assert tiktok_request is not None
    assert tiktok_request.target_label == "tiktok:MX"
    physical = {
        row["target_label"]: row
        for row in release.get_run(run["run_id"])["targets"]
    }
    assert physical["shopee:MY"]["attempts"] == 0


def test_shopee_global_control_dispatches_once_before_regions_with_exact_counts(
    tmp_path,
):
    targets = ["shopee:PH", "shopee:MY"]
    release, plan, run = _approved_context(tmp_path, targets=targets)
    dispatch_calls = []
    region_contexts = []

    def dispatch(request):
        dispatch_calls.append(request.target_label)
        if request.target_label == SHOPEE_GLOBAL_TARGET:
            _record_confirmed_global_writes(request)
            return _verified_global_result(request)
        region_contexts.append(dict(request.shared_resource_context))
        return DispatchTargetResult(
            canonical_status=SUCCEEDED,
            reason_category="POST_WRITE",
            reason_scope="TARGET",
            reason_code="regional_readback_exact",
            reason_detail="regional official readback is exact",
            external_writes=("shopee:regional_publish",),
            external_id=f"internal-{request.target_label}",
            submission_accepted=True,
            readback_verified=True,
        )

    registry = _registry(
        targets,
        global_prepare_override=_ensure_new_global_prepare,
        dispatch_override=dispatch,
    )
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan, run=run, product_revision=31, registry=registry
    )
    worker = OneClickReleaseWorker(
        control,
        lambda: registry,
        dispatch_enabled=lambda: True,
    )
    assert worker.advance_once(job["job_id"]) is True
    prepared = control.get_job(job_id=job["job_id"])
    assert prepared["schema_version"] == "oneclick-release-status/v2"
    assert prepared["storefront_count"] == 2
    assert len(prepared["shared_controls"]) == 1
    assert prepared["shared_controls"][0]["target_label"] == (
        SHOPEE_GLOBAL_TARGET
    )
    assert prepared["targets"][0]["dependency"]["prerequisite"][
        "target_label"
    ] == SHOPEE_GLOBAL_TARGET

    assert worker.advance_once(job["job_id"]) is True
    after_global = control.get_job(job_id=job["job_id"])
    shared = after_global["shared_controls"][0]
    assert shared["status"] == SUCCEEDED
    assert shared["dispatch_ledger"][
        "cumulative_external_write_count"
    ] == 8
    assert shared["dispatch_ledger"][
        "confirmed_external_write_count_lower_bound"
    ] == 8
    assert shared["dispatch_ledger"][
        "possible_external_write_count_upper_bound"
    ] == 8
    assert release.get_run(run["run_id"])["targets"][0]["attempts"] == 0
    assert worker.advance_once(job["job_id"]) is True
    assert worker.advance_once(job["job_id"]) is True
    assert worker.advance_once(job["job_id"]) is False
    assert dispatch_calls == [
        SHOPEE_GLOBAL_TARGET,
        "shopee:PH",
        "shopee:MY",
    ]
    assert len(region_contexts) == 2
    assert region_contexts[0] == region_contexts[1]
    assert set(region_contexts[0]) == {
        "schema_version",
        "policy_version",
        "owner_key",
        "master_lineage_digest",
        "global_identity_digest",
        "master_evidence_digest",
    }
    physical = release.get_run(run["run_id"])["targets"]
    assert [row["target_label"] for row in physical] == targets
    assert [row["attempts"] for row in physical] == [1, 1]
    assert control.pending_outcome_receipts() != []
    assert all(
        row["target_label"] != SHOPEE_GLOBAL_TARGET
        for row in control.pending_outcome_receipts()
    )


def test_shopee_global_pending_write_intent_recovers_without_region_claim(
    tmp_path,
):
    targets = ["shopee:VN"]
    release, plan, run = _approved_context(tmp_path, targets=targets)
    registry = _registry(
        targets,
        global_prepare_override=_ensure_new_global_prepare,
    )
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan, run=run, product_revision=31, registry=registry
    )
    control.prepare_job(job["job_id"], registry)
    request = control.claim_next_dispatch(job["job_id"], registry)
    assert request.target_label == SHOPEE_GLOBAL_TARGET
    control.record_dispatch_progress(
        request,
        (SHOPEE_IMAGE_UPLOAD_WRITE,),
        "image_upload-1",
        {"count": 1},
        None,
        0,
        1,
        "PRE_INVOCATION_INTENT",
    )
    assert control.recover_interrupted_dispatches() == 1
    projected = control.get_job(job_id=job["job_id"])
    shared = projected["shared_controls"][0]
    assert shared["status"] == RECONCILIATION_REQUIRED
    assert shared["dispatch_ledger"][
        "cumulative_external_write_count"
    ] is None
    assert shared["dispatch_ledger"][
        "confirmed_external_write_count_lower_bound"
    ] == 0
    assert shared["dispatch_ledger"][
        "possible_external_write_count_upper_bound"
    ] == 1
    assert projected["targets"][0]["dependency"]["state"] == "BLOCKED"
    assert projected["targets"][0]["dependency"]["prerequisite"][
        "reason"
    ]["code"] == "worker_interrupted_dispatch_unknown"
    assert control.claim_next_dispatch(job["job_id"], registry) is None
    physical = release.get_run(run["run_id"])["targets"][0]
    assert physical["status"] == "PENDING"
    assert physical["attempts"] == 0
    assert control.pending_outcome_receipts() == []


@pytest.mark.parametrize("confirmed_before_reject", [0, 1])
def test_shopee_global_explicit_rejection_resolves_matching_write_intent(
    tmp_path,
    confirmed_before_reject,
):
    targets = ["shopee:TH"]
    release, plan, run = _approved_context(tmp_path, targets=targets)
    registry = _registry(
        targets,
        global_prepare_override=_ensure_new_global_prepare,
    )
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan, run=run, product_revision=31, registry=registry
    )
    control.prepare_job(job["job_id"], registry)
    request = control.claim_next_dispatch(job["job_id"], registry)
    if confirmed_before_reject:
        control.record_dispatch_progress(
            request,
            (SHOPEE_IMAGE_UPLOAD_WRITE,),
            "image_upload-1",
            {"count": 1},
            None,
            0,
            1,
            "PRE_INVOCATION_INTENT",
        )
        control.record_dispatch_progress(
            request,
            (SHOPEE_IMAGE_UPLOAD_WRITE,),
            "image_upload-1",
            {"count": 1},
            1,
            1,
            1,
            "POST_RESPONSE_CONFIRMED",
        )
    prior_classes = (
        (SHOPEE_IMAGE_UPLOAD_WRITE,)
        if confirmed_before_reject
        else ()
    )
    next_count = confirmed_before_reject + 1
    intended_classes = (SHOPEE_IMAGE_UPLOAD_WRITE,)
    control.record_dispatch_progress(
        request,
        intended_classes,
        f"image_upload-{next_count}",
        {"count": next_count},
        None,
        confirmed_before_reject,
        next_count,
        "PRE_INVOCATION_INTENT",
    )
    control.record_dispatch_progress(
        request,
        prior_classes,
        f"image_upload-{next_count}",
        {"count": next_count, "rejected": True},
        confirmed_before_reject,
        confirmed_before_reject,
        confirmed_before_reject,
        "POST_RESPONSE_REJECTED",
    )
    result = (
        DispatchTargetResult(
            canonical_status=FAILED_PRE_SUBMIT,
            reason_category="PRE_SUBMIT",
            reason_scope="TARGET",
            reason_code="global_first_write_rejected",
            reason_detail="official API rejected without creating anything",
            external_writes=(),
            external_write_count=0,
        )
        if confirmed_before_reject == 0
        else DispatchTargetResult(
            canonical_status=RECONCILIATION_REQUIRED,
            reason_category="POST_WRITE",
            reason_scope="TARGET",
            reason_code="later_global_write_rejected",
            reason_detail="one prior upload exists and later write was rejected",
            external_writes=prior_classes,
            external_write_count=1,
            confirmed_external_write_count_lower_bound=1,
            possible_external_write_count_upper_bound=1,
            dispatch_outcome_unknown=False,
        )
    )
    projected = control.record_dispatch_result(request, result)
    shared = projected["shared_controls"][0]
    assert shared["result"]["external_write_count"] == (
        confirmed_before_reject
    )
    assert shared["dispatch_ledger"][
        "cumulative_external_write_count"
    ] == confirmed_before_reject
    assert projected["targets"][0]["dependency"]["state"] == "BLOCKED"
    assert release.get_run(run["run_id"])["targets"][0]["attempts"] == 0
    assert control.claim_next_dispatch(job["job_id"], registry) is None


def test_shopee_global_known_writes_then_readback_mismatch_preserves_exact_count(
    tmp_path,
):
    targets = ["shopee:MY"]
    release, plan, run = _approved_context(tmp_path, targets=targets)

    def dispatch(request):
        assert request.target_label == SHOPEE_GLOBAL_TARGET
        _record_confirmed_global_writes(request)
        raise DispatchInvocationError(
            "official global readback did not converge",
            external_writes=SHOPEE_GLOBAL_WRITE_CLASSES,
            dispatch_outcome_unknown=False,
            external_write_count=8,
            confirmed_external_write_count_lower_bound=8,
            possible_external_write_count_upper_bound=8,
        )

    registry = _registry(
        targets,
        global_prepare_override=_ensure_new_global_prepare,
        dispatch_override=dispatch,
    )
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan, run=run, product_revision=31, registry=registry
    )
    worker = OneClickReleaseWorker(
        control,
        lambda: registry,
        dispatch_enabled=lambda: True,
    )
    assert worker.advance_once(job["job_id"]) is True
    assert worker.advance_once(job["job_id"]) is True
    projected = control.get_job(job_id=job["job_id"])
    shared = projected["shared_controls"][0]
    assert shared["status"] == RECONCILIATION_REQUIRED
    assert shared["result"]["external_write_count"] == 8
    assert shared["result"][
        "confirmed_external_write_count_lower_bound"
    ] == 8
    assert shared["result"][
        "possible_external_write_count_upper_bound"
    ] == 8
    assert shared["result"]["dispatch_outcome_unknown"] is False
    assert projected["targets"][0]["dependency"]["state"] == "BLOCKED"


def test_shopee_global_untrusted_exception_without_open_intent_is_unknown_one(
    tmp_path,
):
    targets = ["shopee:MY"]
    release, plan, run = _approved_context(tmp_path, targets=targets)

    def dispatch(request):
        assert request.target_label == SHOPEE_GLOBAL_TARGET
        raise RuntimeError("untrusted adapter failed before recording intent")

    registry = _registry(
        targets,
        global_prepare_override=_ensure_new_global_prepare,
        dispatch_override=dispatch,
    )
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan, run=run, product_revision=31, registry=registry
    )
    worker = OneClickReleaseWorker(
        control,
        lambda: registry,
        dispatch_enabled=lambda: True,
    )
    assert worker.advance_once(job["job_id"]) is True
    assert worker.advance_once(job["job_id"]) is True
    shared = control.get_job(job_id=job["job_id"])[
        "shared_controls"
    ][0]
    assert shared["status"] == RECONCILIATION_REQUIRED
    assert shared["dispatch_ledger"][
        "confirmed_external_write_count_lower_bound"
    ] == 0
    assert shared["dispatch_ledger"][
        "possible_external_write_count_upper_bound"
    ] == 1
    assert "UNKNOWN" in shared["dispatch_ledger"][
        "cumulative_external_write_classes"
    ]
    assert release.get_run(run["run_id"])["targets"][0]["attempts"] == 0


def test_shopee_global_open_then_exception_preserves_same_unknown_interval(
    tmp_path,
):
    targets = ["shopee:MY"]
    release, plan, _run = _approved_context(tmp_path, targets=targets)

    def dispatch(request):
        request.progress_recorder(
            request,
            (SHOPEE_IMAGE_UPLOAD_WRITE,),
            "image_upload-1",
            {"count": 1},
            None,
            0,
            1,
            "PRE_INVOCATION_INTENT",
        )
        raise RuntimeError("transport ended after invocation")

    registry = _registry(
        targets,
        global_prepare_override=_ensure_new_global_prepare,
        dispatch_override=dispatch,
    )
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan, run=_run, product_revision=31, registry=registry
    )
    worker = OneClickReleaseWorker(
        control,
        lambda: registry,
        dispatch_enabled=lambda: True,
    )
    assert worker.advance_once(job["job_id"]) is True
    assert worker.advance_once(job["job_id"]) is True
    shared = control.get_job(job_id=job["job_id"])[
        "shared_controls"
    ][0]
    assert shared["status"] == RECONCILIATION_REQUIRED
    assert shared["dispatch_ledger"][
        "cumulative_external_write_count"
    ] is None
    assert shared["dispatch_ledger"][
        "confirmed_external_write_count_lower_bound"
    ] == 0
    assert shared["dispatch_ledger"][
        "possible_external_write_count_upper_bound"
    ] == 1
    assert control.recover_interrupted_dispatches() == 0
    assert worker.advance_once(job["job_id"]) is False


def test_shopee_global_open_recorder_failure_prevents_network_invocation(
    tmp_path,
    monkeypatch,
):
    targets = ["shopee:VN"]
    release, plan, _run = _approved_context(tmp_path, targets=targets)
    network_calls = []

    def dispatch(request):
        request.progress_recorder(
            request,
            (SHOPEE_IMAGE_UPLOAD_WRITE,),
            "image_upload-1",
            {"count": 1},
            None,
            0,
            1,
            "PRE_INVOCATION_INTENT",
        )
        network_calls.append(True)
        raise AssertionError("network must not be reached")

    registry = _registry(
        targets,
        global_prepare_override=_ensure_new_global_prepare,
        dispatch_override=dispatch,
    )
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan, run=_run, product_revision=31, registry=registry
    )
    worker = OneClickReleaseWorker(
        control,
        lambda: registry,
        dispatch_enabled=lambda: True,
    )
    assert worker.advance_once(job["job_id"]) is True

    def fail_recorder(*_args, **_kwargs):
        raise sqlite3.OperationalError("durable intent write failed")

    monkeypatch.setattr(control, "record_dispatch_progress", fail_recorder)
    assert worker.advance_once(job["job_id"]) is True
    assert network_calls == []
    shared = control.get_job(job_id=job["job_id"])[
        "shared_controls"
    ][0]
    assert shared["status"] == RECONCILIATION_REQUIRED
    assert shared["dispatch_ledger"][
        "possible_external_write_count_upper_bound"
    ] == 1


def test_shopee_global_occurrence_is_unique_and_resolution_is_idempotent(
    tmp_path,
):
    targets = ["shopee:TH"]
    release, plan, run = _approved_context(tmp_path, targets=targets)
    registry = _registry(
        targets,
        global_prepare_override=_ensure_new_global_prepare,
    )
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan, run=run, product_revision=31, registry=registry
    )
    control.prepare_job(job["job_id"], registry)
    request = control.claim_next_dispatch(job["job_id"], registry)
    args = (
        request,
        (SHOPEE_IMAGE_UPLOAD_WRITE,),
        "image_upload-1",
        {"count": 1},
        None,
        0,
        1,
        "PRE_INVOCATION_INTENT",
    )
    control.record_dispatch_progress(*args)
    with pytest.raises(AdapterContractError):
        control.record_dispatch_progress(*args)
    resolution = (
        request,
        (SHOPEE_IMAGE_UPLOAD_WRITE,),
        "image_upload-1",
        {"count": 1},
        1,
        1,
        1,
        "POST_RESPONSE_CONFIRMED",
    )
    control.record_dispatch_progress(*resolution)
    control.record_dispatch_progress(*resolution)
    with pytest.raises(AdapterContractError):
        control.record_dispatch_progress(
            request,
            (SHOPEE_IMAGE_UPLOAD_WRITE,),
            "image_upload-1",
            {"count": 1},
            0,
            0,
            0,
            "POST_RESPONSE_REJECTED",
        )
    with pytest.raises(AdapterContractError):
        control.record_dispatch_progress(*args)
    with sqlite3.connect(release.path) as connection:
        occurrence = connection.execute(
            """
            SELECT status, resolution_count
            FROM oneclick_release_write_occurrences
            WHERE job_id = ? AND target_label = ?
            """,
            (job["job_id"], SHOPEE_GLOBAL_TARGET),
        ).fetchall()
        progress_events = connection.execute(
            """
            SELECT COUNT(*) FROM oneclick_release_events
            WHERE job_id = ? AND target_label = ?
              AND event_type = 'DISPATCH_PROGRESS'
            """,
            (job["job_id"], SHOPEE_GLOBAL_TARGET),
        ).fetchone()[0]
    assert occurrence == [("CONFIRMED", 1)]
    assert progress_events == 2


def test_shopee_global_rejects_count_and_sequence_drift(tmp_path):
    targets = ["shopee:PH"]
    release, plan, run = _approved_context(tmp_path, targets=targets)
    registry = _registry(
        targets,
        global_prepare_override=_ensure_new_global_prepare,
    )
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan, run=run, product_revision=31, registry=registry
    )
    control.prepare_job(job["job_id"], registry)
    request = control.claim_next_dispatch(job["job_id"], registry)
    with pytest.raises(AdapterContractError):
        control.record_dispatch_progress(
            request,
            (SHOPEE_GLOBAL_WRITE,),
            "global_create-1",
            {"count": 1},
            None,
            0,
            1,
            "PRE_INVOCATION_INTENT",
        )
    request = request.__class__(
        **{
            **request.__dict__,
            "progress_recorder": control.record_dispatch_progress,
        }
    )
    _record_confirmed_global_writes(request, image_count=6)
    with pytest.raises(AdapterContractError):
        control.record_dispatch_result(
            request,
            _verified_global_result(request, image_count=5),
        )
    assert release.get_run(run["run_id"])["targets"][0]["attempts"] == 0


def test_shopee_global_claim_is_concurrent_once_without_physical_target(
    tmp_path,
):
    targets = ["shopee:TH", "shopee:VN"]
    release, plan, run = _approved_context(tmp_path, targets=targets)
    registry = _registry(
        targets,
        global_prepare_override=_ensure_new_global_prepare,
    )
    control = OneClickReleaseStore(release.path)
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
    requests = [value for value in claimed if value is not None]
    assert len(requests) == 1
    assert requests[0].target_label == SHOPEE_GLOBAL_TARGET
    assert all(
        row["attempts"] == 0
        for row in release.get_run(run["run_id"])["targets"]
    )


def test_v1_prepared_command_cannot_claim_after_v2_upgrade(tmp_path):
    targets = ["shopee:PH"]
    release, plan, run = _approved_context(tmp_path, targets=targets)
    registry = _registry(targets)
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan, run=run, product_revision=31, registry=registry
    )
    control.prepare_job(job["job_id"], registry)
    with sqlite3.connect(release.path) as connection:
        row = connection.execute(
            """
            SELECT command_json
            FROM oneclick_release_targets
            WHERE job_id = ? AND target_label = ?
            """,
            (job["job_id"], "shopee:PH"),
        ).fetchone()
        command = json.loads(row[0])
        command["schema_version"] = "release-target-prepared-command/v1"
        encoded = json.dumps(
            command,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        connection.execute(
            """
            UPDATE oneclick_release_targets
            SET command_json = ?, command_digest = ?
            WHERE job_id = ? AND target_label = ?
            """,
            (encoded, digest, job["job_id"], "shopee:PH"),
        )
    with pytest.raises(
        SystemicIdentityError,
        match="schema or identity is stale",
    ):
        control.claim_next_dispatch(job["job_id"], registry)
    physical = release.get_run(run["run_id"])["targets"][0]
    assert physical["status"] == "PENDING"
    assert physical["attempts"] == 0


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
    public_target = public["targets"][0]
    assert public_target["status"] == expected_control
    assert public_target["dispatch_ledger"][
        "cumulative_external_write_classes"
    ] == public_target["result"]["cumulative_external_write_classes"]
    assert public_target["dispatch_ledger"][
        "cumulative_external_write_count"
    ] == public_target["result"]["cumulative_external_write_count"]
    assert public_target["dispatch_ledger"][
        "confirmed_external_write_count_lower_bound"
    ] == public_target["result"][
        "confirmed_external_write_count_lower_bound"
    ]
    assert public_target["dispatch_ledger"][
        "possible_external_write_count_upper_bound"
    ] == public_target["result"][
        "possible_external_write_count_upper_bound"
    ]
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
        ("shopee:regional_publish",),
        "regional_publish_confirmed",
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
        "shopee:regional_publish",
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
    assert preview["control_row_count"] == 2
    assert preview["blocked"] == ["ozon:RU"]
    assert preview["runnable_target_count"] == 0
    assert preview["preparation_pending_count"] == 10
    assert preview["will_dispatch"] == []
    assert preview["start_allowed"] is True


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
    assert preview["preparation_pending_count"] == 1
    assert tiktok_preview["dependency"]["state"] == "WAITING"
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
    regional_preview = next(
        row
        for row in preview["targets"]
        if row["target_label"] == "shopee:MY"
    )
    assert regional_preview["next_action"] == "prepare_batch"
    assert regional_preview["next_action_target"] == "shopee:GLOBAL"

    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan,
        run=run,
        product_revision=31,
        registry=registry,
    )
    projected = control.prepare_job(job["job_id"], registry)
    regional_projected = next(
        row
        for row in projected["targets"]
        if row["target_label"] == "shopee:MY"
    )
    assert regional_projected["next_action"] == expected_action
    assert regional_projected["next_action_target"] == "shopee:MY"
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
            ("shopee:regional_publish",),
            "regional_publish_confirmed",
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
        "shopee:regional_publish",
        "UNKNOWN",
    ]
    pending = control.pending_outcome_receipts()
    assert pending[0]["receipt"]["dispatch"]["external_write_count"] is None
    assert pending[0]["receipt"]["dispatch"][
        "external_write_classes"
    ] == ["shopee:regional_publish", "UNKNOWN"]


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


def test_verified_warning_is_success_with_pending_manual_review_and_no_replay(
    tmp_path,
):
    from domains.data_operations import adapt_release_outcome_receipt

    targets = ["shopee:MY"]
    release, plan, run = _approved_context(tmp_path, targets=targets)
    dispatch_calls = []
    observation_digest = _digest("regional-observation")

    def dispatch(request):
        dispatch_calls.append(request.target_label)
        return DispatchTargetResult(
            canonical_status=SUCCEEDED_MANUAL_REVIEW,
            reason_category="POST_WRITE",
            reason_scope="TARGET",
            reason_code="official_readback_verified_with_warning",
            reason_detail="official readback verified with observation warning",
            external_writes=("shopee:regional_publish",),
            external_id="internal-shopee-my",
            submission_accepted=True,
            readback_verified=True,
            evidence={
                "manual_review": True,
                "rule_ids": [
                    "copy:language_signal_weak",
                    "global_image:rehosted_order_unverifiable",
                ],
                "observation_evidence_digest": observation_digest,
            },
        )

    registry = _registry(targets, dispatch_override=dispatch)
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan, run=run, product_revision=31, registry=registry
    )
    worker = OneClickReleaseWorker(
        control, lambda: registry, dispatch_enabled=lambda: True
    )

    assert worker.advance_once(job["job_id"]) is True
    assert worker.advance_once(job["job_id"]) is True
    assert worker.advance_once(job["job_id"]) is False
    assert worker.advance_once(job["job_id"]) is False
    assert dispatch_calls == ["shopee:MY"]

    projected = control.get_job(job_id=job["job_id"])
    target = projected["targets"][0]
    assert projected["phase"] == "WAITING_MANUAL_ACCEPTANCE"
    assert projected["requires_human"] is True
    assert projected["summary"]["manual_after_submit"] == ["shopee:MY"]
    assert target["status"] == SUCCEEDED_MANUAL_REVIEW
    assert target["requires_human"] is True
    assert target["manual_after_submit"] is True
    assert target["next_action"] == "review_verified_observation_warning"
    assert target["result"]["manual_review"] is True
    assert target["result"]["rule_ids"] == [
        "copy:language_signal_weak",
        "global_image:rehosted_order_unverifiable",
    ]
    assert target["result"]["observation_digests"] == [
        observation_digest
    ]

    physical = release.get_run(run["run_id"])["targets"][0]
    assert physical["status"] == "SUCCEEDED"
    assert physical["attempts"] == 1
    readback_evidence = physical["readback"]["evidence"]
    assert readback_evidence["readback_verified"] is True
    assert readback_evidence["canonical_status"] == (
        SUCCEEDED_MANUAL_REVIEW
    )
    assert readback_evidence["manual_review"] is True
    assert readback_evidence["rule_ids"] == target["result"]["rule_ids"]
    assert readback_evidence["observation_digests"] == [
        observation_digest
    ]

    pending = control.pending_outcome_receipts()
    assert len(pending) == 1
    receipt = pending[0]["receipt"]
    assert receipt["outcome"]["class"] == "SUCCESS"
    assert receipt["readback"]["status"] == "VERIFIED"
    assert receipt["manual"]["status"] == "PENDING"
    assert receipt["manual"]["evidence_digest"]
    assert receipt["reconciliation"]["status"] == "NOT_REQUIRED"
    assert receipt["counts"]["manual_reviews"] == 1
    fact = adapt_release_outcome_receipt(receipt)
    assert fact.outcome_class == "SUCCESS"
    assert fact.manual_status == "PENDING"
    assert fact.readback_status == "VERIFIED"

    pending_target = control.get_job(job_id=job["job_id"])["targets"][0]
    acceptance_evidence = {
        "source": "kyle_verified_shopee_observation_review",
        "manual_review_accepted": True,
        "observation_evidence_digest": observation_digest,
        "job_identity_digest": hashlib.sha256(
            job["job_id"].encode("utf-8")
        ).hexdigest(),
        "result_evidence_digest": pending_target["result"][
            "evidence_digest"
        ],
        "readback_evidence_digest": pending_target["result"][
            "evidence_digest"
        ],
        "outcome_receipt_digest": pending_target["outcome_receipt"][
            "receipt_digest"
        ],
        "observation_evidence_digests": [observation_digest],
    }
    accepted = control.record_manual_acceptance(
        run_id=run["run_id"],
        target_label="shopee:MY",
        verified_by="Kyle",
        user_verified=True,
        verification_evidence=acceptance_evidence,
    )
    assert accepted["idempotent"] is False
    assert accepted["job"]["phase"] == "SUCCEEDED"
    assert accepted["job"]["requires_human"] is False
    assert accepted["job"]["targets"][0]["status"] == SUCCEEDED
    assert accepted["job"]["targets"][0]["result"][
        "manual_review_status"
    ] == "ACCEPTED"
    assert release.get_run(run["run_id"])["targets"][0]["status"] == (
        "SUCCEEDED"
    )
    assert worker.advance_once(job["job_id"]) is False
    assert dispatch_calls == ["shopee:MY"]
    replay = control.record_manual_acceptance(
        run_id=run["run_id"],
        target_label="shopee:MY",
        verified_by="Kyle",
        user_verified=True,
        verification_evidence=acceptance_evidence,
    )
    assert replay["idempotent"] is True

    durable = release.path.read_bytes()
    assert b"raw_response" not in durable


@pytest.mark.parametrize(
    "evidence",
    [
        None,
        {},
        {"manual_review": False},
        {
            "manual_review": True,
            "rule_ids": [],
            "observation_evidence_digest": "a" * 64,
        },
        {
            "manual_review": True,
            "rule_ids": ["copy:warning"],
        },
        {
            "manual_review": True,
            "rule_ids": ["copy:warning"],
            "observation_evidence_digest": "not-a-digest",
        },
        {
            "manual_review": True,
            "rule_ids": ["copy:warning", "copy:warning"],
            "observation_evidence_digest": "a" * 64,
        },
        {
            "manual_review": True,
            "rule_ids": ["copy:warning"],
            "observation_evidence_digest": "a" * 64,
            "raw_response": {"title": "sensitive"},
        },
    ],
)
def test_manual_review_success_rejects_incomplete_redacted_evidence(evidence):
    with pytest.raises(AdapterContractError):
        DispatchTargetResult.from_value(
            {
                "canonical_status": SUCCEEDED_MANUAL_REVIEW,
                "reason_category": "POST_WRITE",
                "reason_scope": "TARGET",
                "reason_code": "verified_warning",
                "reason_detail": "verified warning",
                "external_writes": ["shopee:regional_publish"],
                "external_id": "internal-id",
                "submission_accepted": True,
                "readback_verified": True,
                "dispatch_outcome_unknown": False,
                "evidence": evidence,
            }
        )


def test_manual_acceptance_closes_api_less_dual_ledgers_and_replays_zero(
    tmp_path,
):
    targets = ["miaoshou:COMMON", "tiktok:MX"]
    release, plan, run = _approved_context(tmp_path, targets=targets)
    dispatch_calls = []

    def dispatch(request):
        dispatch_calls.append(request.target_label)
        if request.target_label == "miaoshou:COMMON":
            return DispatchTargetResult(
                canonical_status=SUCCEEDED,
                reason_category="CAPABILITY",
                reason_scope="TARGET",
                reason_code="common_exact",
                reason_detail="COMMON exact",
                external_writes=(),
                external_id="common-internal",
                readback_verified=True,
            )
        return DispatchTargetResult(
            canonical_status=SUBMITTED_UNVERIFIED,
            reason_category="POST_WRITE",
            reason_scope="TARGET",
            reason_code="platform_submission_accepted",
            reason_detail="platform submission accepted",
            external_writes=("miaoshou:tiktok_publish",),
            external_id="tiktok-internal",
            submission_accepted=True,
        )

    registry = _registry(
        targets,
        dispatch_override=dispatch,
        manual_labels=("tiktok:MX",),
    )
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan, run=run, product_revision=31, registry=registry
    )
    worker = OneClickReleaseWorker(
        control, lambda: registry, dispatch_enabled=lambda: True
    )
    while worker.advance_once(job["job_id"]):
        pass
    assert dispatch_calls == ["miaoshou:COMMON", "tiktok:MX"]
    assert control.get_job(job_id=job["job_id"])["phase"] == (
        "WAITING_MANUAL_ACCEPTANCE"
    )

    evidence = {
        "source": "kyle_marketplace_console_inspection",
        "marketplace_product_id": "platform-product-123",
        "identity_matches": True,
        "seller_sku_matches": True,
        "single_listing_for_sku": True,
        "title_matches": True,
        "price_matches": True,
        "images_match": True,
        "logistics_match": True,
    }
    invalid = {**evidence, "images_match": False}
    for verified_by, user_verified in (
        ("Other", True),
        ("Kyle", False),
    ):
        with pytest.raises(AdapterContractError):
            control.record_manual_acceptance(
                run_id=run["run_id"],
                target_label="tiktok:MX",
                verified_by=verified_by,
                user_verified=user_verified,
                verification_evidence=evidence,
            )
    with pytest.raises(AdapterContractError):
        control.record_manual_acceptance(
            run_id=run["run_id"],
            target_label="tiktok:MX",
            verified_by="Kyle",
            user_verified=True,
            verification_evidence=invalid,
        )
    before = control.get_job(job_id=job["job_id"])
    assert before["targets"][1]["status"] == SUBMITTED_UNVERIFIED
    assert release.get_run(run["run_id"])["targets"][1]["attempts"] == 1
    original_outcome_sample_count = len(
        control.pending_outcome_receipts()
    )

    closed = control.record_manual_acceptance(
        run_id=run["run_id"],
        target_label="tiktok:MX",
        verified_by="Kyle",
        user_verified=True,
        verification_evidence=evidence,
    )
    assert closed["idempotent"] is False
    assert closed["external_writes_performed"] == []
    assert closed["job"]["phase"] == "SUCCEEDED"
    assert closed["job"]["requires_human"] is False
    closed_target = closed["job"]["targets"][1]
    assert closed_target["status"] == SUCCEEDED
    assert closed_target["requires_human"] is False
    assert closed_target["result"]["manual_review_status"] == "ACCEPTED"
    manual_resolution = closed_target["outcome_receipt"][
        "manual_resolution"
    ]
    assert manual_resolution["schema_version"] == (
        "release-outcome-manual-acceptance/v1"
    )
    assert manual_resolution["consumer_status"] == "PENDING"
    pending_resolutions = (
        control.pending_manual_acceptance_resolutions()
    )
    assert len(pending_resolutions) == 1
    resolution = pending_resolutions[0]
    assert resolution["resolution_digest"] == manual_resolution[
        "resolution_digest"
    ]
    assert resolution["resolution"]["manual"] == {
        "status": "ACCEPTED",
        "reviewer_role": "approved_release_actor",
    }
    assert resolution["resolution"]["external_writes_performed"] == []
    assert "marketplace_product_id" not in json.dumps(
        resolution["resolution"]
    )
    assert len(control.pending_outcome_receipts()) == (
        original_outcome_sample_count
    )

    physical = release.get_run(run["run_id"])["targets"][1]
    assert physical["status"] == "MANUALLY_VERIFIED"
    assert physical["storage_status"] == "FAILED"
    assert physical["attempts"] == 1
    assert dispatch_calls == ["miaoshou:COMMON", "tiktok:MX"]
    assert worker.advance_once(job["job_id"]) is False

    replay = control.record_manual_acceptance(
        run_id=run["run_id"],
        target_label="tiktok:MX",
        verified_by="Kyle",
        user_verified=True,
        verification_evidence=evidence,
    )
    assert replay["idempotent"] is True
    assert replay["external_writes_performed"] == []
    assert replay["job"]["phase"] == "SUCCEEDED"
    assert len(control.pending_manual_acceptance_resolutions()) == 1
    assert len(control.pending_outcome_receipts()) == (
        original_outcome_sample_count
    )
    control.record_manual_acceptance_consumer_result(
        job_id=resolution["job_id"],
        target_label=resolution["target_label"],
        attempt=resolution["attempt"],
        resolution_digest=resolution["resolution_digest"],
        fact_digest="e" * 64,
        error_code=None,
    )
    assert control.pending_manual_acceptance_resolutions() == []
    projected_resolution = control.get_job(
        job_id=job["job_id"]
    )["targets"][1]["outcome_receipt"]["manual_resolution"]
    assert projected_resolution["consumer_status"] == "SUCCEEDED"
    assert projected_resolution["fact_digest"] == "e" * 64
    assert len(control.pending_outcome_receipts()) == (
        original_outcome_sample_count
    )
    with pytest.raises(SystemicIdentityError):
        control.record_manual_acceptance(
            run_id=run["run_id"],
            target_label="tiktok:MX",
            verified_by="Kyle",
            user_verified=True,
            verification_evidence={
                **evidence,
                "marketplace_product_id": "different-product",
            },
        )
    with sqlite3.connect(release.path) as connection:
        event_count = connection.execute(
            """
            SELECT COUNT(*) FROM oneclick_release_events
            WHERE job_id = ? AND target_label = ?
              AND event_type = 'TARGET_MANUAL_ACCEPTANCE'
            """,
            (job["job_id"], "tiktok:MX"),
        ).fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                UPDATE oneclick_release_events
                SET event_type = 'TAMPERED'
                WHERE job_id = ? AND target_label = ?
                  AND event_type = 'TARGET_MANUAL_ACCEPTANCE'
                """,
                (job["job_id"], "tiktok:MX"),
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                UPDATE oneclick_release_manual_acceptances
                SET resolution_digest = ?
                WHERE job_id = ? AND target_label = ?
                """,
                ("f" * 64, job["job_id"], "tiktok:MX"),
            )
    assert event_count == 1


def test_manual_acceptance_rejects_receipt_drift_and_reconciliation(tmp_path):
    targets = ["shopee:MY"]
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
            canonical_status=RECONCILIATION_REQUIRED,
            reason_category="POST_WRITE",
            reason_scope="TARGET",
            reason_code="readback_failed",
            reason_detail="readback failed",
            external_writes=("shopee:regional_publish",),
            dispatch_outcome_unknown=True,
        ),
    )
    with pytest.raises(SystemicIdentityError):
        control.record_manual_acceptance(
            run_id=run["run_id"],
            target_label="shopee:MY",
            verified_by="Kyle",
            user_verified=True,
            verification_evidence={
                "manual_review_accepted": True,
                "observation_evidence_digest": "a" * 64,
            },
        )

    targets2 = ["tiktok:MX", "miaoshou:COMMON"]
    targets2 = ["miaoshou:COMMON", "tiktok:MX"]
    release2, plan2, run2 = _approved_context(
        tmp_path / "drift",
        targets=targets2,
    )
    registry2 = _registry(targets2, manual_labels=("tiktok:MX",))
    control2 = OneClickReleaseStore(release2.path)
    job2 = control2.ensure_job(
        plan=plan2, run=run2, product_revision=31, registry=registry2
    )
    control2.prepare_job(job2["job_id"], registry2)
    common = control2.claim_next_dispatch(job2["job_id"], registry2)
    control2.record_dispatch_result(
        common,
        DispatchTargetResult(
            canonical_status=SUCCEEDED,
            reason_category="CAPABILITY",
            reason_scope="TARGET",
            reason_code="common_exact",
            reason_detail="COMMON exact",
            external_writes=(),
            external_id="common-id",
            readback_verified=True,
        ),
    )
    mx = control2.claim_next_dispatch(job2["job_id"], registry2)
    control2.record_dispatch_result(
        mx,
        DispatchTargetResult(
            canonical_status=SUBMITTED_UNVERIFIED,
            reason_category="POST_WRITE",
            reason_scope="TARGET",
            reason_code="accepted",
            reason_detail="accepted",
            external_writes=("miaoshou:tiktok_publish",),
            external_id="mx-id",
            submission_accepted=True,
        ),
    )
    with sqlite3.connect(release2.path) as connection:
        connection.execute(
            """
            UPDATE release_target_submissions
            SET evidence_digest = ?
            WHERE run_id = ? AND target_label = ?
            """,
            ("f" * 64, run2["run_id"], "tiktok:MX"),
        )
    with pytest.raises(SystemicIdentityError):
        control2.record_manual_acceptance(
            run_id=run2["run_id"],
            target_label="tiktok:MX",
            verified_by="Kyle",
            user_verified=True,
            verification_evidence={
                "marketplace_product_id": "mx-product",
                "identity_matches": True,
                "seller_sku_matches": True,
                "single_listing_for_sku": True,
                "title_matches": True,
                "price_matches": True,
                "images_match": True,
                "logistics_match": True,
            },
        )
    assert control2.get_job(job_id=job2["job_id"])["targets"][1][
        "status"
    ] == SUBMITTED_UNVERIFIED


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
