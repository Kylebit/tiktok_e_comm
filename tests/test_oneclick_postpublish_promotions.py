from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace

import pytest

from domains.channel_operations.oneclick_release_adapters import (
    production_adapter_registry,
)
from modules.products import server as product_server
from modules.tiktok.oneclick_promotion import (
    TikTokPromotionBlocked,
    TikTokPromotionDispatchError,
    TikTokPromotionTransport,
    configure_tiktok_promotion_transport_factory,
    dispatch_postpublish_promotion,
    prepare_postpublish_promotion,
)
from shared_platform.oneclick_release_controlplane import (
    AdapterRegistration,
    build_batch_preview,
    DispatchInvocationError,
    DispatchTargetResult,
    EXACT_READY_AUTOMATIC,
    OneClickReleaseStore,
    OneClickReleaseWorker,
    PrepareTargetResult,
    preview_run_for_plan,
    RECONCILIATION_REQUIRED,
    SUCCEEDED,
)
from shared_platform.postpublish_promotions import (
    PROMOTION_SELECTION_POLICY,
    TIKTOK_PROMOTION_WRITE_CLASS,
    approved_postpublish_promotion_policy,
    build_approved_postpublish_promotion_policy,
    enabled_promotion_action_targets,
)
from shared_platform.release_store import ReleaseStore, preview_release_plan
from tests.test_oneclick_release_controlplane import (
    _digest,
    _plan_payload,
    _registry,
)
from tests.test_product_release_v1 import _dashboard


def _promotion_payload(targets=("miaoshou:COMMON", "tiktok:LH_PH")):
    payload = _plan_payload(targets=list(targets))
    payload["approved_postpublish_promotion_policy"] = (
        build_approved_postpublish_promotion_policy(
            approval_reference=(
                "Kyle-20260730-existing-ongoing-direct-discount"
            )
        )
    )
    payload["pricing"] = {
        "selected_targets": {
            "tiktok:LH_PH": {
                "store_prices": [
                    {
                        "target_key": "lh_ph",
                        "list_price": 200,
                        "currency": "PHP",
                    }
                ]
            }
        }
    }
    return payload


def _prepare_request(payload=None, target="promotion:tiktok:LH_PH"):
    payload = payload or _promotion_payload()
    return SimpleNamespace(
        target_label=target,
        immutable_plan_payload=payload,
        prerequisite_context={
            "schema_version": "postpublish-prerequisite-readback/v1",
            "target_label": target.removeprefix("promotion:"),
            "external_id": "product-1",
            "readback_evidence_digest": "a" * 64,
            "result_digest": "b" * 64,
        },
        idempotency_key="promotion:test:1",
    )


class _FixtureTransport:
    def __init__(self, *, direct_count=1, put_mode="success"):
        self.direct_count = direct_count
        self.put_mode = put_mode
        self.calls = []
        self.written = False

    def transport(self):
        return TikTokPromotionTransport(
            list_shops=self.list_shops,
            search_activities=self.search,
            get_activity=self.activity,
            get_product=self.product,
            put_activity_products=self.put,
        )

    def list_shops(self):
        self.calls.append(("shops",))
        return {
            "code": 0,
            "data": {
                "shops": [
                    {
                        "id": "7676267",
                        "name": "LivelyHive",
                        "region": "PH",
                        "cipher": "cipher-ph",
                    }
                ]
            },
        }

    def search(self, query, body):
        self.calls.append(("search", deepcopy(query), deepcopy(body)))
        page = query.get("page_token", "")
        rows = [
            {
                "id": "other-1",
                "activity_type": "FLASH_SALE",
                "status": "ONGOING",
            }
        ]
        if page == "next-1":
            rows = [
                {
                    "id": f"direct-{index}",
                    "activity_type": "DIRECT_DISCOUNT",
                    "status": "ONGOING",
                }
                for index in range(self.direct_count)
            ]
        return {
            "code": 0,
            "data": {
                "activities": rows,
                "total_count": 1 + self.direct_count,
                "next_page_token": "next-1" if not page else "",
            },
        }

    def activity(self, activity_id, query):
        self.calls.append(("activity", activity_id, deepcopy(query)))
        products = (
            [{"id": "product-1", "discount": "32"}]
            if self.written
            else []
        )
        return {
            "code": 0,
            "data": {
                "id": activity_id,
                "activity_type": "DIRECT_DISCOUNT",
                "status": "ONGOING",
                "begin_time": 1,
                "end_time": 4102444800,
                "products": products,
            },
        }

    def product(self, product_id, query):
        self.calls.append(("product", product_id, deepcopy(query)))
        return {
            "code": 0,
            "data": {
                "id": product_id,
                "product_status": "ACTIVATE",
                "skus": [
                    {
                        "seller_sku": "0954",
                        "price": {
                            "sale_price": "200",
                            "currency": "PHP",
                        },
                    }
                ],
            },
        }

    def put(self, activity_id, query, body):
        self.calls.append(
            ("put", activity_id, deepcopy(query), deepcopy(body))
        )
        if self.put_mode == "timeout":
            raise TimeoutError("write timeout")
        if self.put_mode == "rejected":
            return {"code": 40001, "message": "rejected"}
        self.written = True
        return {"code": 0, "data": {}}


def test_policy_is_plan_bound_deterministic_and_opt_in_only():
    policy = build_approved_postpublish_promotion_policy(
        approval_reference=(
            "Kyle-20260730-existing-ongoing-direct-discount"
        )
    )
    approved = approved_postpublish_promotion_policy(
        {"approved_postpublish_promotion_policy": policy}
    )
    assert approved["selection_policy"] == (
        PROMOTION_SELECTION_POLICY
    )
    assert enabled_promotion_action_targets(
        {"approved_postpublish_promotion_policy": policy},
        ["tiktok:LH_PH", "tiktok:MX", "tiktok:HB_PH", "ozon:RU"],
    ) == ["promotion:tiktok:LH_PH"]
    assert enabled_promotion_action_targets(
        {},
        ["tiktok:LH_PH"],
    ) == []
    drifted = deepcopy(policy)
    drifted["discounts"]["tiktok"] = 31
    with pytest.raises(ValueError):
        approved_postpublish_promotion_policy(
            {"approved_postpublish_promotion_policy": drifted}
        )


def test_server_binds_new_policy_and_ignores_client_injection():
    dashboard = _dashboard()
    dashboard["publication_scope"]["selected_labels"] = [
        "miaoshou:COMMON",
        "tiktok:LH_PH",
    ]
    dashboard["pricing_review"]["target_pricing"]["tiktok:LH_PH"] = (
        dashboard["pricing_review"]["target_pricing"].pop("tiktok:MX")
    )
    dashboard["omnichannel_preview"]["targets"][1]["site"] = "LH_PH"
    dashboard["listing_copy"]["candidates"][0]["site"] = "PH"
    dashboard["approved_postpublish_promotion_policy"] = {
        "client": "must-not-win"
    }
    payload, blockers = product_server._release_plan_payload_from_dashboard(
        dashboard,
        bind_shopee_global_plan=False,
    )
    assert blockers == []
    policy = approved_postpublish_promotion_policy(payload)
    assert policy["discounts"]["tiktok"] == 32
    assert "client" not in policy
    old_payload = _plan_payload(targets=["tiktok:LH_PH"])
    assert "approved_postpublish_promotion_policy" not in old_payload
    before = preview_release_plan(old_payload)["payload_digest"]
    after = preview_release_plan(
        {
            **old_payload,
            "approved_postpublish_promotion_policy": policy,
        }
    )["payload_digest"]
    assert before != after


def test_prepare_paginates_every_page_and_selects_unique_activity():
    fixture = _FixtureTransport()
    configure_tiktok_promotion_transport_factory(fixture.transport)
    try:
        result = prepare_postpublish_promotion(_prepare_request())
    finally:
        configure_tiktok_promotion_transport_factory(None)
    assert result["classification"] == EXACT_READY_AUTOMATIC
    assert result["command"]["discount_percent"] == 32
    assert result["proof"]["complete_activity_pagination"] is True
    searches = [call for call in fixture.calls if call[0] == "search"]
    assert len(searches) == 2
    assert searches[0][1] == {
        "shop_cipher": "cipher-ph",
        "page_size": 100,
    }
    assert searches[0][2] == {
        "page_size": 100,
        "status": "ONGOING",
    }
    assert searches[1][1]["page_token"] == "next-1"


@pytest.mark.parametrize("direct_count", [0, 2])
def test_prepare_blocks_zero_or_ambiguous_direct_activities(direct_count):
    fixture = _FixtureTransport(direct_count=direct_count)
    configure_tiktok_promotion_transport_factory(fixture.transport)
    try:
        with pytest.raises(TikTokPromotionBlocked) as caught:
            prepare_postpublish_promotion(_prepare_request())
    finally:
        configure_tiktok_promotion_transport_factory(None)
    assert caught.value.reason_code == (
        "unique_ongoing_direct_discount_unavailable"
    )
    assert not any(call[0] == "put" for call in fixture.calls)


def test_dispatch_opens_one_occurrence_writes_once_and_reads_back():
    fixture = _FixtureTransport()
    configure_tiktok_promotion_transport_factory(fixture.transport)
    try:
        prepared = prepare_postpublish_promotion(_prepare_request())
        progress = []

        def recorder(*args, **kwargs):
            progress.append((args, kwargs))

        request = SimpleNamespace(
            target_label="promotion:tiktok:LH_PH",
            command={"payload": prepared["command"]},
            proof={"payload": prepared["proof"]},
            progress_recorder=recorder,
            prepared_command_digest="c" * 64,
        )
        result = dispatch_postpublish_promotion(request)
    finally:
        configure_tiktok_promotion_transport_factory(None)
    assert result["canonical_status"] == SUCCEEDED
    assert result["external_writes"] == (
        TIKTOK_PROMOTION_WRITE_CLASS,
    )
    assert len([call for call in fixture.calls if call[0] == "put"]) == 1
    assert [value[1]["write_boundary"] for value in progress] == [
        "PRE_INVOCATION_INTENT",
        "POST_RESPONSE_CONFIRMED",
    ]


@pytest.mark.parametrize(
    ("mode", "expected_boundary"),
    [
        ("timeout", "PRE_INVOCATION_INTENT"),
        ("rejected", "POST_RESPONSE_REJECTED"),
    ],
)
def test_dispatch_timeout_is_one_possible_write_and_rejection_is_exact_zero(
    mode,
    expected_boundary,
):
    fixture = _FixtureTransport(put_mode=mode)
    configure_tiktok_promotion_transport_factory(fixture.transport)
    try:
        prepared = prepare_postpublish_promotion(_prepare_request())
        progress = []

        def recorder(*args, **kwargs):
            progress.append((args, kwargs))

        request = SimpleNamespace(
            target_label="promotion:tiktok:LH_PH",
            command={"payload": prepared["command"]},
            proof={"payload": prepared["proof"]},
            progress_recorder=recorder,
            prepared_command_digest="c" * 64,
        )
        if mode == "timeout":
            with pytest.raises(TikTokPromotionDispatchError) as caught:
                dispatch_postpublish_promotion(request)
            assert caught.value.external_write_count is None
            assert (
                caught.value.possible_external_write_count_upper_bound
                == 1
            )
        else:
            result = dispatch_postpublish_promotion(request)
            assert result["canonical_status"] == "FAILED_PRE_SUBMIT"
            assert result["external_write_count"] == 0
            assert result["external_writes"] == ()
    finally:
        configure_tiktok_promotion_transport_factory(None)
    assert len([call for call in fixture.calls if call[0] == "put"]) == 1
    assert progress[-1][1]["write_boundary"] == expected_boundary


@pytest.mark.parametrize(
    "fault",
    ["row_nonmapping", "total_mismatch", "cursor_loop"],
)
def test_activity_pagination_shape_faults_block_before_write(fault):
    fixture = _FixtureTransport()
    original = fixture.search

    def broken(query, body):
        result = original(query, body)
        if fault == "row_nonmapping" and not query.get("page_token"):
            result["data"]["activities"].append("bad")
            result["data"]["total_count"] += 1
        elif fault == "total_mismatch" and query.get("page_token"):
            result["data"]["total_count"] += 1
        elif fault == "cursor_loop":
            result["data"]["next_page_token"] = "next-1"
        return result

    transport = fixture.transport()
    transport = TikTokPromotionTransport(
        list_shops=transport.list_shops,
        search_activities=broken,
        get_activity=transport.get_activity,
        get_product=transport.get_product,
        put_activity_products=transport.put_activity_products,
    )
    configure_tiktok_promotion_transport_factory(lambda: transport)
    try:
        with pytest.raises(TikTokPromotionBlocked):
            prepare_postpublish_promotion(_prepare_request())
    finally:
        configure_tiktok_promotion_transport_factory(None)
    assert not any(call[0] == "put" for call in fixture.calls)


def _promotion_registration(prepare, dispatch):
    return AdapterRegistration(
        adapter_name="postpublish_promotion",
        target_labels=(
            "promotion:tiktok:LH_PH",
            "promotion:shopee:MY",
        ),
        prepare=prepare,
        dispatch=dispatch,
        policy_digest=hashlib.sha256(b"promotion").hexdigest(),
        prepare_is_read_only=True,
        consumes_prepared_command=True,
        preserves_idempotency_key=True,
        reports_truthful_receipt=True,
    )


def _approved_promotion_context(tmp_path, targets):
    release = ReleaseStore(tmp_path / "release.db")
    payload = _promotion_payload(targets=tuple(targets))
    created = release.create_plan(payload)
    release.approve_plan(
        created["plan_id"],
        approved_by="Kyle",
        user_approved=True,
        confirmation_token=created["confirmation_token"],
    )
    return (
        release,
        release.get_plan(created["plan_id"]),
        release.start_run(created["plan_id"]),
    )


def test_readonly_batch_preview_includes_server_owned_promotion_action(
    tmp_path,
):
    targets = ["miaoshou:COMMON", "tiktok:LH_PH"]
    _release, plan, _run = _approved_promotion_context(tmp_path, targets)
    registry = _registry(targets)
    registry["postpublish_promotion"] = _promotion_registration(
        lambda _request: None,
        lambda _request: None,
    )

    preview = build_batch_preview(
        plan=plan,
        run=preview_run_for_plan(plan),
        product_revision=31,
        registry=registry,
    )

    action = next(
        row
        for row in preview["postpublish_actions"]
        if row["target_label"] == "promotion:tiktok:LH_PH"
    )
    assert action["status"] == "PENDING"
    assert action["classification"] == "PREPARE_PENDING"
    assert action["runnable_now"] is False
    assert action["dependency"]["state"] == "WAITING"


def test_same_job_defers_promotion_and_never_rewrites_primary_success(
    tmp_path,
):
    targets = ["miaoshou:COMMON", "tiktok:LH_PH"]
    release, plan, run = _approved_promotion_context(tmp_path, targets)
    calls = []
    registry = _registry(targets)

    def prepare(request):
        calls.append(("prepare", request.target_label))
        assert request.prerequisite_context["target_label"] == (
            "tiktok:LH_PH"
        )
        return PrepareTargetResult(
            classification=EXACT_READY_AUTOMATIC,
            reason_category="CAPABILITY",
            reason_scope="TARGET",
            reason_code="promotion_exact",
            reason_detail="promotion exact",
            command={"kind": "promotion"},
            proof={"kind": "promotion-proof"},
        )

    def dispatch(request):
        calls.append(("dispatch", request.target_label))
        request.progress_recorder(
            request,
            (TIKTOK_PROMOTION_WRITE_CLASS,),
            "promotion_apply-1",
            {"phase": "intent"},
            None,
            0,
            1,
            "PRE_INVOCATION_INTENT",
        )
        request.progress_recorder(
            request,
            (TIKTOK_PROMOTION_WRITE_CLASS,),
            "promotion_apply-1",
            {"phase": "confirmed"},
            1,
            1,
            1,
            "POST_RESPONSE_CONFIRMED",
        )
        return DispatchTargetResult(
            canonical_status=SUCCEEDED,
            reason_category="CAPABILITY",
            reason_scope="TARGET",
            reason_code="promotion_readback_exact",
            reason_detail="promotion readback exact",
            external_writes=(TIKTOK_PROMOTION_WRITE_CLASS,),
            external_write_count=1,
            confirmed_external_write_count_lower_bound=1,
            possible_external_write_count_upper_bound=1,
            external_id="sha256:" + "d" * 64,
            submission_accepted=True,
            readback_verified=True,
            evidence={"readback_digest": "e" * 64},
        )

    registry["postpublish_promotion"] = _promotion_registration(
        prepare, dispatch
    )
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan,
        run=run,
        product_revision=31,
        registry=registry,
    )
    worker = OneClickReleaseWorker(
        control,
        lambda: registry,
        dispatch_enabled=lambda: True,
    )
    assert worker.advance_once(job["job_id"]) is True
    assert not any(row[0] == "prepare" for row in calls)
    assert worker.advance_once(job["job_id"]) is True  # COMMON
    assert worker.advance_once(job["job_id"]) is True  # storefront
    base_after_publish = release.get_run(run["run_id"])
    assert next(
        row
        for row in base_after_publish["targets"]
        if row["target_label"] == "tiktok:LH_PH"
    )["status"] == SUCCEEDED
    assert worker.advance_once(job["job_id"]) is True  # promo prepare
    assert worker.advance_once(job["job_id"]) is True  # promo dispatch
    assert worker.advance_once(job["job_id"]) is False
    final = control.get_job(job_id=job["job_id"])
    action = next(
        row
        for row in final["postpublish_actions"]
        if row["target_label"] == "promotion:tiktok:LH_PH"
    )
    assert action["status"] == SUCCEEDED
    assert action["result"]["external_write_count"] == 1
    assert action["result"]["external_write_classes"] == [
        TIKTOK_PROMOTION_WRITE_CLASS
    ]
    assert action["dispatch_ledger"][
        "cumulative_external_write_count"
    ] == 1
    assert action["dispatch_ledger"][
        "cumulative_external_write_classes"
    ] == [TIKTOK_PROMOTION_WRITE_CLASS]
    success_outcome = next(
        row
        for row in control.pending_outcome_receipts()
        if row["target_label"] == "promotion:tiktok:LH_PH"
    )
    assert success_outcome["receipt"]["dispatch"][
        "external_write_count"
    ] == 1
    assert success_outcome["receipt"]["dispatch"][
        "external_write_classes"
    ] == [TIKTOK_PROMOTION_WRITE_CLASS]
    base_final = release.get_run(run["run_id"])
    assert base_final == base_after_publish
    assert calls.count(("dispatch", "promotion:tiktok:LH_PH")) == 1


def test_promotion_open_intent_restart_requires_reconciliation_and_replay_zero(
    tmp_path,
):
    targets = ["miaoshou:COMMON", "tiktok:LH_PH"]
    release, plan, run = _approved_promotion_context(tmp_path, targets)
    dispatches = []
    registry = _registry(targets)
    registry["postpublish_promotion"] = _promotion_registration(
        lambda _request: PrepareTargetResult(
            classification=EXACT_READY_AUTOMATIC,
            reason_category="CAPABILITY",
            reason_scope="TARGET",
            reason_code="promotion_exact",
            reason_detail="promotion exact",
            command={"kind": "promotion"},
            proof={"kind": "promotion-proof"},
        ),
        lambda request: dispatches.append(request),
    )
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan,
        run=run,
        product_revision=31,
        registry=registry,
    )
    worker = OneClickReleaseWorker(
        control, lambda: registry, dispatch_enabled=lambda: True
    )
    worker.advance_once(job["job_id"])
    worker.advance_once(job["job_id"])
    worker.advance_once(job["job_id"])
    worker.advance_once(job["job_id"])
    request = control.claim_next_dispatch(job["job_id"], registry)
    request = replace(
        request,
        progress_recorder=control.record_dispatch_progress,
    )
    control.record_dispatch_progress(
        request,
        (TIKTOK_PROMOTION_WRITE_CLASS,),
        "promotion_apply-1",
        {"phase": "intent"},
        None,
        0,
        1,
        "PRE_INVOCATION_INTENT",
    )
    recovered = OneClickReleaseStore(release.path)
    assert recovered.recover_interrupted_dispatches() == 1
    state = recovered.get_job(job_id=job["job_id"])
    action = state["postpublish_actions"][0]
    assert action["status"] == RECONCILIATION_REQUIRED
    assert action["dispatch_ledger"][
        "possible_external_write_count_upper_bound"
    ] == 1
    assert action["dispatch_ledger"][
        "cumulative_external_write_count"
    ] is None
    assert action["dispatch_ledger"][
        "cumulative_external_write_classes"
    ] == [TIKTOK_PROMOTION_WRITE_CLASS, "UNKNOWN"]
    recovery_outcome = next(
        row
        for row in recovered.pending_outcome_receipts()
        if row["target_label"] == "promotion:tiktok:LH_PH"
    )
    assert recovery_outcome["receipt"]["dispatch"][
        "external_write_count"
    ] is None
    assert recovery_outcome["receipt"]["dispatch"][
        "external_write_classes"
    ] == [TIKTOK_PROMOTION_WRITE_CLASS, "UNKNOWN"]
    primary = next(
        row
        for row in release.get_run(run["run_id"])["targets"]
        if row["target_label"] == "tiktok:LH_PH"
    )
    assert primary["status"] == SUCCEEDED
    replay = OneClickReleaseWorker(
        recovered, lambda: registry, dispatch_enabled=lambda: True
    )
    assert replay.advance_once(job["job_id"]) is False
    assert dispatches == []


def test_promotion_transport_unknown_is_consistent_across_terminal_ledgers(
    tmp_path,
):
    targets = ["miaoshou:COMMON", "tiktok:LH_PH"]
    release, plan, run = _approved_promotion_context(tmp_path, targets)
    registry = _registry(targets)

    def dispatch(request):
        request.progress_recorder(
            request,
            (TIKTOK_PROMOTION_WRITE_CLASS,),
            "promotion_apply-1",
            {"phase": "intent"},
            None,
            0,
            1,
            "PRE_INVOCATION_INTENT",
        )
        raise DispatchInvocationError(
            "promotion transport outcome unknown",
            external_writes=(TIKTOK_PROMOTION_WRITE_CLASS,),
            dispatch_outcome_unknown=True,
            external_write_count=None,
            confirmed_external_write_count_lower_bound=0,
            possible_external_write_count_upper_bound=1,
        )

    registry["postpublish_promotion"] = _promotion_registration(
        lambda _request: PrepareTargetResult(
            classification=EXACT_READY_AUTOMATIC,
            reason_category="CAPABILITY",
            reason_scope="TARGET",
            reason_code="promotion_exact",
            reason_detail="promotion exact",
            command={"kind": "promotion"},
            proof={"kind": "promotion-proof"},
        ),
        dispatch,
    )
    control = OneClickReleaseStore(release.path)
    job = control.ensure_job(
        plan=plan,
        run=run,
        product_revision=31,
        registry=registry,
    )
    worker = OneClickReleaseWorker(
        control, lambda: registry, dispatch_enabled=lambda: True
    )
    for _ in range(5):
        assert worker.advance_once(job["job_id"]) is True
    action = control.get_job(job_id=job["job_id"])[
        "postpublish_actions"
    ][0]
    assert action["status"] == RECONCILIATION_REQUIRED
    assert action["result"]["external_write_count"] is None
    assert action["result"]["external_write_classes"] == [
        TIKTOK_PROMOTION_WRITE_CLASS,
        "UNKNOWN",
    ]
    assert action["dispatch_ledger"][
        "cumulative_external_write_count"
    ] is None
    assert action["dispatch_ledger"][
        "confirmed_external_write_count_lower_bound"
    ] == 0
    assert action["dispatch_ledger"][
        "possible_external_write_count_upper_bound"
    ] == 1
    assert action["dispatch_ledger"][
        "cumulative_external_write_classes"
    ] == [TIKTOK_PROMOTION_WRITE_CLASS, "UNKNOWN"]
    outcome = next(
        row
        for row in control.pending_outcome_receipts()
        if row["target_label"] == "promotion:tiktok:LH_PH"
    )
    assert outcome["receipt"]["dispatch"]["external_write_count"] is None
    assert outcome["receipt"]["dispatch"][
        "external_write_classes"
    ] == [TIKTOK_PROMOTION_WRITE_CLASS, "UNKNOWN"]


def test_promotion_targets_are_not_registered_in_direct_store_mvp():
    registry = production_adapter_registry()
    assert set(registry) == {
        "miaoshou-direct-store/v1",
        "shopee_cnsc_publish",
    }
    assert all(
        not target.startswith("promotion:")
        for target in registry["miaoshou-direct-store/v1"].target_labels
    )
