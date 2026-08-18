import json
import importlib
from http.server import ThreadingHTTPServer
from itertools import permutations
import threading
from types import SimpleNamespace
import urllib.error
import urllib.request

import pytest

from modules.products import server as product_server


@pytest.fixture
def product_http_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), product_server.Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _request(url, *, method="GET", payload=None):
    encoded = (
        json.dumps(payload).encode("utf-8") if payload is not None else None
    )
    request = urllib.request.Request(
        url,
        data=encoded,
        method=method,
        headers=(
            {"Content-Type": "application/json"}
            if encoded is not None
            else {}
        ),
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_publish_http_is_short_202_job_start_not_legacy_loop(
    monkeypatch, product_http_server
):
    calls = []

    def start(payload):
        calls.append(payload)
        return 202, {
            "ok": True,
            "accepted": True,
            "external_writes_performed": [],
            "job": {
                "job_id": "oneclick-job:test",
                "phase": "PENDING",
            },
        }

    monkeypatch.setattr(product_server, "_start_tiktok_release", start)
    monkeypatch.setattr(
        product_server,
        "_publish_selected_release",
        lambda _payload: pytest.fail("legacy synchronous publisher was called"),
    )

    status, response = _request(
        product_http_server + "/api/product-workspace/publish",
        method="POST",
        payload={
            "offer_id": "3838616043",
            "plan_id": "omnichannel:test",
            "confirmation_token": "server-echo-only",
            "confirm_publish": True,
        },
    )

    assert status == 202
    assert response["job"]["phase"] == "PENDING"
    assert response["external_writes_performed"] == []
    assert len(calls) == 1


def test_publish_post_cannot_reopen_legacy_job_before_collectbox_step(monkeypatch):
    calls = []
    plan = {
        "plan_id": "omnichannel:test",
        "targets": ["tiktok:MX", "tiktok:LH_MY"],
    }

    class ReleaseStore:
        @staticmethod
        def start_run(plan_id):
            assert plan_id == plan["plan_id"]
            calls.append("start_run")
            return {"run_id": "release-run:test"}

        @staticmethod
        def get_plan(plan_id):
            assert plan_id == plan["plan_id"]
            return plan

    class ControlStore:
        @staticmethod
        def ensure_job(**_kwargs):
            calls.append("ensure_job")
            return {
                "job_id": "oneclick-job:legacy",
                "phase": "BLOCKED",
                "batch_sequence": 1,
            }

        @staticmethod
        def set_dispatch_capability(job_id, *, enabled):
            assert job_id == "oneclick-job:legacy"
            assert enabled is True
            calls.append("set_dispatch_capability")
            return {
                "job_id": job_id,
                "phase": "BLOCKED",
                "batch_sequence": 1,
            }

        @staticmethod
        def start_explicit_batch(job_id):
            assert job_id == "oneclick-job:legacy"
            calls.append("start_explicit_batch")
            return {
                "job_id": job_id,
                "phase": "PENDING",
                "batch_sequence": 2,
            }

    context = {
        "plan": plan,
        "payload": {"product_revision": 31},
        "store": ReleaseStore(),
    }
    monkeypatch.setattr(
        product_server,
        "_oneclick_approved_context",
        lambda _data: (context, None),
    )
    monkeypatch.setattr(
        product_server,
        "_oneclick_adapter_registry",
        lambda: {"miaoshou-direct-store/v1": object()},
    )
    monkeypatch.setattr(
        product_server,
        "_oneclick_control_store",
        lambda: ControlStore(),
    )
    monkeypatch.setattr(
        product_server,
        "_oneclick_dispatch_capability",
        lambda: {"enabled": True},
    )
    monkeypatch.setattr(
        product_server,
        "_wake_oneclick_worker",
        lambda job_id: calls.append(f"wake:{job_id}"),
    )
    monkeypatch.setattr(
        product_server,
        "_project_oneclick_dispatch_capability",
        lambda job: job,
    )
    monkeypatch.setattr(
        product_server,
        "_collectbox_platform_succeeded",
        lambda _plan_id, _platform: False,
    )

    status, response = product_server._start_oneclick_release(
        {
            "confirm_publish": True,
            "plan_id": plan["plan_id"],
        },
        batch_scope="TIKTOK",
        require_collectbox_platform="TIKTOK",
    )

    assert status == 409
    assert response["error"]["code"] == "step1_collectbox_required"
    assert response["canonical_next_action"]["action"] == (
        "start_collectbox_action"
    )
    assert calls == []


def test_legacy_reduced_tiktok_request_does_not_start_oneclick_batch(
    monkeypatch,
):
    calls = []
    plan = {
        "plan_id": "omnichannel:test",
        "targets": ["tiktok:MX", "tiktok:LH_MY"],
    }

    class ReleaseStore:
        @staticmethod
        def start_run(plan_id):
            assert plan_id == plan["plan_id"]
            calls.append("start_run")
            return {"run_id": "release-run:test"}

        @staticmethod
        def get_plan(plan_id):
            assert plan_id == plan["plan_id"]
            return plan

    class CollectboxStore:
        @staticmethod
        def status(*, plan_id):
            assert plan_id == plan["plan_id"]
            return {
                "action": {
                    "status": "SUCCEEDED",
                    "platforms": [
                        {"platform": "TIKTOK", "status": "SUCCEEDED"},
                        {"platform": "SHOPEE", "status": "SUCCEEDED"},
                    ],
                }
            }

        @staticmethod
        def internal_tiktok_publish_contexts(*, plan_id):
            assert plan_id == plan["plan_id"]
            return {"tiktok:MX": {"fixture": "server-internal-proof"}}

    class ControlStore:
        @staticmethod
        def ensure_job(**_kwargs):
            calls.append("ensure_job")
            return {
                "job_id": "oneclick-job:test",
                "phase": "PENDING",
                "targets": [
                    {"target_label": "tiktok:MX"},
                    {"target_label": "tiktok:LH_MY"},
                    {"target_label": "shopee:GLOBAL"},
                    {"target_label": "ozon:RU"},
                ],
            }

        @staticmethod
        def set_dispatch_capability(job_id, *, enabled):
            assert job_id == "oneclick-job:test"
            assert enabled is True
            calls.append("set_dispatch_capability")
            return ControlStore.ensure_job()

        @staticmethod
        def start_explicit_batch(job_id, *, target_labels):
            assert job_id == "oneclick-job:test"
            assert target_labels == ("tiktok:MX", "tiktok:LH_MY")
            calls.append("start_tiktok_batch")
            return {
                **ControlStore.ensure_job(),
                "batch_scope": "TIKTOK",
                "batch_scope_targets": list(target_labels),
            }

    context = {
        "plan": plan,
        "payload": {"product_revision": 31},
        "store": ReleaseStore(),
    }
    monkeypatch.setattr(
        product_server,
        "_oneclick_approved_context",
        lambda _data: (context, None),
    )
    monkeypatch.setattr(
        product_server,
        "_collectbox_action_store",
        lambda: CollectboxStore(),
    )
    monkeypatch.setattr(
        product_server,
        "_oneclick_adapter_registry",
        lambda: {"miaoshou-direct-store/v1": object()},
    )
    monkeypatch.setattr(
        product_server,
        "_oneclick_control_store",
        lambda: ControlStore(),
    )
    monkeypatch.setattr(
        product_server,
        "_oneclick_dispatch_capability",
        lambda: {"enabled": True},
    )
    monkeypatch.setattr(
        product_server,
        "_wake_oneclick_worker",
        lambda _job_id: (_ for _ in ()).throw(
            AssertionError("the final HTTP result must not use a background wake")
        ),
    )
    monkeypatch.setattr(
        product_server,
        "_project_oneclick_dispatch_capability",
        lambda job: job,
    )
    monkeypatch.setattr(
        product_server,
        "_complete_oneclick_platform_batch",
        lambda **kwargs: (
            calls.append(
                (
                    "complete_tiktok_batch",
                    kwargs["job_id"],
                    kwargs["target_labels"],
                    kwargs["batch_scope"],
                )
            )
            or (
                200,
                {
                    "schema_version": (
                        "miaoshou-platform-publish-result/v1"
                    ),
                    "ok": True,
                    "platform": "TIKTOK",
                    "success": True,
                    "message": "TikTok published",
                    "target_count": 2,
                    "successful_target_count": 2,
                    "failed_targets": [],
                    "retryable": True,
                },
            )
        ),
    )

    status, response = product_server._start_tiktok_release(
        {
            "confirm_publish": True,
            "plan_id": plan["plan_id"],
        }
    )

    assert status == 409
    assert response["success"] is False
    assert response["platform"] == "TIKTOK"
    assert response["error"]["code"] == "tiktok_approved_snapshot_invalid"
    assert "start_tiktok_batch" not in calls


def test_shared_control_v2_is_canonical_action_without_storefront_count(
    monkeypatch,
):
    monkeypatch.setattr(
        product_server,
        "_oneclick_dispatch_capability",
        lambda: {
            "schema_version": "oneclick-dispatch-capability/v1",
            "enabled": True,
            "source": "test",
            "reason_code": "enabled",
            "next_action": None,
        },
    )
    projected = product_server._project_oneclick_dispatch_capability(
        {
            "schema_version": "oneclick-release-status/v2",
            "phase": "BLOCKED",
            "storefront_count": 1,
            "runnable_target_count": 0,
            "summary": {
                "will_dispatch": [],
                "manual_after_submit": [],
                "blocked": ["shopee:MY"],
                "already_terminal": [],
            },
            "targets": [
                {
                    "target_label": "shopee:MY",
                    "status": "BLOCKED_CAPABILITY",
                    "runnable_now": False,
                    "next_action": "resolve_prerequisite_target",
                    "next_action_target": "shopee:GLOBAL",
                }
            ],
            "shared_controls": [
                {
                    "target_label": "shopee:GLOBAL",
                    "status": "RECONCILIATION_REQUIRED",
                    "runnable_now": False,
                    "next_action": "reconcile_before_any_retry",
                    "next_action_target": "shopee:GLOBAL",
                }
            ],
        }
    )
    assert projected["schema_version"] == "oneclick-release-status/v2"
    assert projected["storefront_count"] == 1
    assert len(projected["targets"]) == 1
    assert len(projected["shared_controls"]) == 1
    assert projected["canonical_next_action"] == {
        "target_label": "shopee:GLOBAL",
        "target_focus": "shopee:GLOBAL",
        "canonical_status": "RECONCILIATION_REQUIRED",
        "action": "reconcile_before_any_retry",
        "runnable": False,
    }


def test_preview_and_status_get_routes_return_server_projection_only(
    monkeypatch, product_http_server
):
    monkeypatch.setattr(
        product_server,
        "_preview_oneclick_release",
        lambda payload: (
            200,
            {
                "ok": True,
                "external_writes_performed": [],
                "preview": {
                    "storefront_count": 11,
                    "will_dispatch": ["shopee:MY"],
                    "blocked": ["ozon:RU"],
                },
                "received": payload,
            },
        ),
    )
    monkeypatch.setattr(
        product_server,
        "_oneclick_release_status",
        lambda payload: (
            200,
            {
                "ok": True,
                "job": {
                    "phase": "WAITING_MANUAL_ACCEPTANCE",
                    "targets": [
                        {
                            "target_label": "tiktok:MX",
                            "status": "SUBMITTED_UNVERIFIED",
                            "next_action": "verify_submission_in_marketplace",
                        }
                    ],
                },
                "received": payload,
            },
        ),
    )

    preview_status, preview = _request(
        product_http_server
        + "/api/product-workspace/publish-preview"
        + "?offer_id=3838616043&plan_id=omnichannel:test"
    )
    job_status, job = _request(
        product_http_server
        + "/api/product-workspace/publish-status"
        + "?job_id=oneclick-job:test"
    )

    assert preview_status == 200
    assert preview["preview"]["storefront_count"] == 11
    assert "confirmation_token" not in preview["received"]
    assert job_status == 200
    assert job["job"]["targets"][0]["next_action"] == (
        "verify_submission_in_marketplace"
    )


def test_preview_rehydrates_publication_targets_from_server_owned_plan(
    monkeypatch,
):
    class PreviewStore:
        @staticmethod
        def get_plan(plan_id):
            assert plan_id == "omnichannel:test"
            return {
                "plan_id": plan_id,
                "product_id": "3838616043",
                "targets": ["tiktok:MX", "shopee:MY"],
            }

    captured = {}

    def capture_dashboard_request(payload):
        captured.update(payload)
        return None, (
            418,
            {
                "ok": False,
                "error": "stop after request identity capture",
            },
        )

    release_store = importlib.import_module("shared_platform.release_store")
    monkeypatch.setattr(
        release_store,
        "default_release_store",
        lambda: PreviewStore(),
    )
    monkeypatch.setattr(
        product_server,
        "_release_dashboard_for_request",
        capture_dashboard_request,
    )

    context, failure = product_server._oneclick_approved_context(
        {
            "offer_id": "3838616043",
            "plan_id": "omnichannel:test",
        },
        require_token=False,
    )

    assert context is None
    assert failure[0] == 418
    assert captured["publication_targets"] == [
        "tiktok:MX",
        "shopee:MY",
    ]


def test_mvp_approved_context_does_not_reblock_start_on_current_content_gate(
    monkeypatch,
):
    payload = {
        "product_id": "3838616043",
        "publication_targets": ["tiktok:MX", "shopee:MY"],
    }
    preview = {"plan_id": "omnichannel:test"}
    plan = {
        "plan_id": "omnichannel:test",
        "product_id": "3838616043",
        "status": "APPROVED",
        "approval": {"status": "APPROVED", "approved_by": "Kyle"},
        "confirmation_token": "approved-token",
        "payload": payload,
        "seller_sku": "0954",
        "sku_reservation": {
            "status": "ACTIVE",
            "plan_id": "omnichannel:test",
            "seller_sku": "0954",
        },
    }

    class ApprovedStore:
        @staticmethod
        def get_plan(plan_id):
            assert plan_id == "omnichannel:test"
            return plan

        @staticmethod
        def preview_plan(candidate):
            assert candidate == payload
            return preview

    release_store = importlib.import_module("shared_platform.release_store")
    monkeypatch.setattr(
        release_store,
        "default_release_store",
        lambda: ApprovedStore(),
    )
    monkeypatch.setattr(
        product_server,
        "_release_dashboard_for_request",
        lambda _data: ({"release_v1": {}}, None),
    )
    monkeypatch.setattr(
        product_server,
        "_release_plan_payload_from_dashboard",
        lambda _dashboard, **_kwargs: (
            payload,
            ["listing copy must be adapted in approved product facts"],
        ),
    )
    monkeypatch.setattr(
        product_server,
        "_approved_plan_matches_current_payload",
        lambda _plan, _preview: True,
    )

    context, failure = product_server._oneclick_approved_context(
        {
            "offer_id": "3838616043",
            "plan_id": "omnichannel:test",
            "confirmation_token": "approved-token",
            "publication_targets": ["tiktok:MX", "shopee:MY"],
        }
    )

    assert failure is None
    assert context is not None
    assert context["plan"] == plan


def test_server_cancels_false_publish_ready_when_no_runnable_target(
    monkeypatch,
):
    class EmptyControlStore:
        @staticmethod
        def get_job(**_kwargs):
            return None

    monkeypatch.setattr(
        product_server,
        "_oneclick_control_store",
        lambda: EmptyControlStore(),
    )
    projected = product_server._apply_oneclick_release_authority(
        {
            "plan": {"plan_id": "omnichannel:test"},
            "publish_ready": True,
            "runnable_target_count": 0,
            "target_recovery_actions": [
                {
                    "target_label": "shopee:PH",
                    "action": "reconcile_before_any_retry",
                    "runnable": False,
                }
            ],
        }
    )

    assert projected["publish_ready"] is False
    assert projected["canonical_next_action"]["target_focus"] == "shopee:PH"
    assert projected["target_recovery_actions"][0]["target_focus"] == (
        "shopee:PH"
    )


def test_manual_acceptance_action_always_has_target_focus(monkeypatch):
    class WaitingControlStore:
        @staticmethod
        def get_job(**_kwargs):
            return {
                "phase": "WAITING_MANUAL_ACCEPTANCE",
                "targets": [
                    {
                        "target_label": "tiktok:GB",
                        "status": "SUBMITTED_UNVERIFIED",
                        "next_action": "verify_submission_in_marketplace",
                    }
                ],
            }

    monkeypatch.setattr(
        product_server,
        "_oneclick_control_store",
        lambda: WaitingControlStore(),
    )
    projected = product_server._apply_oneclick_release_authority(
        {"plan": {"plan_id": "omnichannel:test"}}
    )

    assert projected["publish_ready"] is False
    assert projected["canonical_next_action"] == {
        "target_label": "tiktok:GB",
        "target_focus": "tiktok:GB",
        "canonical_status": "SUBMITTED_UNVERIFIED",
        "action": "verify_submission_in_marketplace",
        "runnable": False,
    }


def test_common_dependency_block_is_the_canonical_focused_action(monkeypatch):
    class DependencyBlockedControlStore:
        @staticmethod
        def get_job(**_kwargs):
            return {
                "phase": "BLOCKED",
                "runnable_target_count": 0,
                "summary": {
                    "will_dispatch": [],
                    "manual_after_submit": [],
                    "blocked": ["miaoshou:COMMON", "tiktok:MX"],
                },
                "targets": [
                    {
                        "target_label": "miaoshou:COMMON",
                        "status": "BLOCKED_CAPABILITY",
                        "next_action": "resolve_channel_capability",
                        "next_action_target": "miaoshou:COMMON",
                        "runnable_now": False,
                    },
                    {
                        "target_label": "tiktok:MX",
                        "status": "READY",
                        "next_action": "resolve_prerequisite",
                        "next_action_target": "miaoshou:COMMON",
                        "runnable_now": False,
                    },
                ],
            }

    monkeypatch.delenv("ORBIT_ONECLICK_EXTERNAL_DISPATCH", raising=False)
    monkeypatch.setattr(
        product_server,
        "_oneclick_control_store",
        lambda: DependencyBlockedControlStore(),
    )
    projected = product_server._apply_oneclick_release_authority(
        {"plan": {"plan_id": "omnichannel:test"}, "publish_ready": True}
    )

    assert projected["publish_ready"] is False
    assert projected["runnable_target_count"] == 0
    assert projected["canonical_next_action"]["target_focus"] == (
        "miaoshou:COMMON"
    )
    assert projected["canonical_next_action"]["runnable"] is False


@pytest.mark.parametrize(
    "ordered_targets",
    list(
        permutations(
            (
                {
                    "target_label": "tiktok:GB",
                    "status": "SUBMITTED_UNVERIFIED",
                    "next_action": "verify_submission_in_marketplace",
                    "runnable_now": False,
                },
                {
                    "target_label": "shopee:MY",
                    "status": "READY",
                    "next_action": "wait_for_worker",
                    "runnable_now": True,
                },
                {
                    "target_label": "ozon:RU",
                    "status": "BLOCKED_INVENTORY",
                    "next_action": "approve_sellable_inventory",
                    "runnable_now": False,
                },
            )
        )
    ),
)
def test_canonical_action_prefers_runnable_target_for_every_row_order(
    monkeypatch,
    ordered_targets,
):
    class MixedControlStore:
        @staticmethod
        def get_job(**_kwargs):
            return {
                "phase": "READY",
                "runnable_target_count": 1,
                "summary": {
                    "will_dispatch": ["shopee:MY"],
                    "manual_after_submit": [],
                    "blocked": ["ozon:RU"],
                },
                "targets": list(ordered_targets),
            }

    monkeypatch.delenv("ORBIT_ONECLICK_EXTERNAL_DISPATCH", raising=False)
    monkeypatch.setattr(
        product_server,
        "_oneclick_control_store",
        lambda: MixedControlStore(),
    )
    projected = product_server._apply_oneclick_release_authority(
        {"plan": {"plan_id": "omnichannel:test"}}
    )

    assert projected["publish_ready"] is True
    assert projected["canonical_next_action"]["target_focus"] == "shopee:MY"
    assert projected["canonical_next_action"]["runnable"] is True


@pytest.mark.parametrize(
    "ordered_targets",
    [
        list(rows)
        for rows in permutations(
            (
                {
                    "target_label": "shopee:PH",
                    "status": "RECONCILIATION_REQUIRED",
                    "next_action": "reconcile_before_any_retry",
                    "runnable_now": False,
                },
                {
                    "target_label": "tiktok:GB",
                    "status": "SUBMITTED_UNVERIFIED",
                    "next_action": "verify_submission_in_marketplace",
                    "runnable_now": False,
                },
            )
        )
    ],
)
def test_no_runnable_target_chooses_stable_focused_recovery(
    monkeypatch,
    ordered_targets,
):
    class RecoveryControlStore:
        @staticmethod
        def get_job(**_kwargs):
            return {
                "phase": "RECONCILIATION_REQUIRED",
                "runnable_target_count": 0,
                "summary": {
                    "will_dispatch": [],
                    "manual_after_submit": ["tiktok:GB"],
                    "blocked": ["shopee:PH"],
                },
                "targets": ordered_targets,
            }

    monkeypatch.delenv("ORBIT_ONECLICK_EXTERNAL_DISPATCH", raising=False)
    monkeypatch.setattr(
        product_server,
        "_oneclick_control_store",
        lambda: RecoveryControlStore(),
    )
    projected = product_server._apply_oneclick_release_authority(
        {"plan": {"plan_id": "omnichannel:test"}}
    )

    assert projected["publish_ready"] is False
    assert projected["canonical_next_action"] == {
        "target_label": "shopee:PH",
        "target_focus": "shopee:PH",
        "canonical_status": "RECONCILIATION_REQUIRED",
        "action": "reconcile_before_any_retry",
        "runnable": False,
    }


@pytest.mark.parametrize(
    "ordered_targets",
    [
        list(rows)
        for rows in permutations(
            (
                {
                    "target_label": "shopee:MY",
                    "status": "BLOCKED_CAPABILITY",
                    "next_action": "review_approved_content_facts",
                    "runnable_now": False,
                },
                {
                    "target_label": "shopee:VN",
                    "status": "BLOCKED_CAPABILITY",
                    "next_action": "review_logistics_policy",
                    "runnable_now": False,
                },
                {
                    "target_label": "ozon:RU",
                    "status": "BLOCKED_CAPABILITY",
                    "next_action": "wait_for_channel_capability",
                    "runnable_now": False,
                },
            )
        )
    ],
)
def test_content_and_logistics_recovery_priority_is_order_independent(
    monkeypatch,
    ordered_targets,
):
    class FactBlockedControlStore:
        @staticmethod
        def get_job(**_kwargs):
            return {
                "phase": "BLOCKED",
                "runnable_target_count": 0,
                "summary": {
                    "will_dispatch": [],
                    "manual_after_submit": [],
                    "blocked": [
                        "shopee:MY",
                        "shopee:VN",
                        "ozon:RU",
                    ],
                },
                "targets": ordered_targets,
            }

    monkeypatch.delenv("ORBIT_ONECLICK_EXTERNAL_DISPATCH", raising=False)
    monkeypatch.setattr(
        product_server,
        "_oneclick_control_store",
        lambda: FactBlockedControlStore(),
    )
    projected = product_server._apply_oneclick_release_authority(
        {"plan": {"plan_id": "omnichannel:test"}}
    )

    assert projected["publish_ready"] is False
    assert projected["canonical_next_action"] == {
        "target_label": "shopee:MY",
        "target_focus": "shopee:MY",
        "canonical_status": "BLOCKED_CAPABILITY",
        "action": "review_approved_content_facts",
        "runnable": False,
    }


def test_dispatch_capability_defaults_enabled_and_explicit_disable_is_clear(
    monkeypatch,
):
    monkeypatch.delenv("ORBIT_ONECLICK_EXTERNAL_DISPATCH", raising=False)
    assert product_server._oneclick_dispatch_enabled() is True
    assert product_server._oneclick_dispatch_capability() == {
        "schema_version": "oneclick-dispatch-capability/v1",
        "enabled": True,
        "source": "server_default",
        "reason_code": "oneclick_dispatch_enabled_by_default",
        "next_action": None,
    }
    enabled = product_server._project_oneclick_dispatch_capability(
        {
            "phase": "READY",
            "runnable_target_count": 1,
            "storefront_count": 1,
            "control_row_count": 0,
            "summary": {
                "will_dispatch": ["shopee:PH"],
                "manual_after_submit": [],
                "blocked": [],
                "already_terminal": [],
            },
            "targets": [
                {
                    "target_label": "shopee:PH",
                    "storefront": True,
                    "classification": "EXACT_READY_AUTOMATIC",
                    "status": "READY",
                    "runnable_now": True,
                    "next_action": "wait_for_worker",
                    "next_action_target": "shopee:PH",
                }
            ],
        }
    )
    assert enabled["canonical_next_action"] == {
        "target_label": "shopee:PH",
        "target_focus": "shopee:PH",
        "canonical_status": "READY",
        "action": "wait_for_worker",
        "runnable": True,
    }

    monkeypatch.setenv("ORBIT_ONECLICK_EXTERNAL_DISPATCH", "false")
    projected = product_server._project_oneclick_dispatch_capability(
        {
            "phase": "READY",
            "runnable_target_count": 1,
            "storefront_count": 1,
            "control_row_count": 0,
            "summary": {
                "will_dispatch": ["shopee:PH"],
                "manual_after_submit": [],
                "blocked": [],
            },
            "targets": [
                {
                    "target_label": "shopee:PH",
                    "storefront": True,
                    "classification": "EXACT_READY_AUTOMATIC",
                    "status": "READY",
                    "runnable_now": True,
                    "next_action": "wait_for_worker",
                    "next_action_target": "shopee:PH",
                }
            ],
        }
    )
    assert projected["phase"] == "BLOCKED"
    assert projected["runnable_target_count"] == 0
    assert projected["canonical_next_action"] == {
        "target_label": None,
        "target_focus": None,
        "canonical_status": "BLOCKED_CAPABILITY",
        "action": "enable_oneclick_dispatch",
        "runnable": False,
    }
    assert projected["targets"][0]["next_action"] == (
        "enable_oneclick_dispatch"
    )
    assert projected["targets"][0]["next_action_target"] is None
    assert len(projected["targets"][0]["reason"]["detail_digest"]) == 64


def test_http_status_never_emits_raw_adapter_detail(
    monkeypatch,
    product_http_server,
):
    raw = (
        "token=RAW_HTTP_SECRET https://merchant.example/raw "
        "title=SECRET_TITLE"
    )
    monkeypatch.setattr(
        product_server,
        "_oneclick_release_status",
        lambda _payload: (
            200,
            {
                "ok": True,
                "job": {
                    "phase": "BLOCKED",
                    "targets": [
                        {
                            "target_label": "shopee:PH",
                            "status": "BLOCKED_CAPABILITY",
                            "reason": {
                                "category": "CAPABILITY",
                                "scope": "TARGET",
                                "code": "adapter_prepare_contract_error",
                                "summary_code": "channel_capability_status",
                                "detail_digest": "a" * 64,
                            },
                        }
                    ],
                },
            },
        ),
    )
    status, response = _request(
        product_http_server
        + "/api/product-workspace/publish-status?job_id=job:test"
    )
    encoded = json.dumps(response)
    assert status == 200
    assert raw not in encoded
    assert "RAW_HTTP_SECRET" not in encoded
    assert "merchant.example" not in encoded


@pytest.mark.parametrize(
    "contract_failure,expected_code",
    [
        (True, "release_outcome_contract_rejected"),
        (False, "release_outcome_consumer_failed"),
    ],
)
def test_outcome_consumer_classifies_errors_without_reopening_release(
    monkeypatch,
    contract_failure,
    expected_code,
):
    class ContractError(ValueError):
        pass

    error = ContractError("bad receipt") if contract_failure else RuntimeError(
        "unexpected consumer bug"
    )
    fake_module = SimpleNamespace(
        ReleaseOutcomeContractError=ContractError,
        adapt_release_outcome_receipt=lambda _receipt: (_ for _ in ()).throw(
            error
        ),
    )
    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda _name: fake_module,
    )

    class Store:
        terminal_state = "SUCCEEDED"
        calls = []

        @staticmethod
        def pending_outcome_receipts(*, limit):
            assert limit == 50
            return [
                {
                    "job_id": "job:1",
                    "target_label": "shopee:PH",
                    "attempt": 1,
                    "receipt_digest": "a" * 64,
                    "receipt": {"schema_version": "release-outcome-receipt/v1"},
                }
            ]

        @classmethod
        def record_outcome_consumer_result(cls, **value):
            cls.calls.append(value)

    product_server._consume_oneclick_outcome_receipts(Store())

    assert Store.calls[0]["error_code"] == expected_code
    assert Store.calls[0]["fact_digest"] is None
    assert Store.terminal_state == "SUCCEEDED"


def test_manual_acceptance_resolution_uses_distinct_05_merge_seam(
    monkeypatch,
):
    class Fact:
        @staticmethod
        def payload():
            return {"fact_digest": "f" * 64}

    fake_module = SimpleNamespace(
        ReleaseOutcomeContractError=ValueError,
        adapt_release_outcome_receipt=lambda _receipt: pytest.fail(
            "resolution must not become a second release sample"
        ),
        adapt_release_outcome_manual_acceptance=lambda _value: Fact(),
    )
    monkeypatch.setattr(
        importlib,
        "import_module",
        lambda _name: fake_module,
    )

    class Store:
        calls = []

        @staticmethod
        def pending_outcome_receipts(*, limit):
            assert limit == 50
            return []

        @staticmethod
        def pending_manual_acceptance_resolutions(*, limit):
            assert limit == 50
            return [
                {
                    "job_id": "job:1",
                    "target_label": "shopee:MY",
                    "attempt": 1,
                    "resolution_digest": "a" * 64,
                    "resolution": {
                        "schema_version": (
                            "release-outcome-manual-acceptance/v1"
                        ),
                    },
                }
            ]

        @classmethod
        def record_manual_acceptance_consumer_result(cls, **value):
            cls.calls.append(value)

    product_server._consume_oneclick_outcome_receipts(Store())
    assert Store.calls == [
        {
            "job_id": "job:1",
            "target_label": "shopee:MY",
            "attempt": 1,
            "resolution_digest": "a" * 64,
            "fact_digest": "f" * 64,
            "error_code": None,
        }
    ]
