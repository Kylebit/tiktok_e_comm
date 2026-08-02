"""Characterization gates for the legacy one-click topology.

The stage-0 fixture preserves the production-shaped state that caused the
2026-08-02 HTTP 409.  Stage 1 closes only its TikTok/GB gate while retaining
the same unmocked HTTP topology and independent platform-route freeze gates.
"""

from copy import deepcopy
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
import threading
import urllib.error
import urllib.request

import pytest

from modules.products import server as product_server


FIXTURE_PATH = (
    Path(__file__).parent
    / "fixtures"
    / "convergence_stage0_legacy_offer.json"
)


@pytest.fixture
def legacy_topology():
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


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


def _post(url, payload):
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _install_legacy_read_context(monkeypatch, topology):
    plan = topology["approved_plan"]
    projection = topology["latest_collectbox_projection"]
    calls = []

    class ReleaseStore:
        def start_run(self, plan_id):
            assert plan_id == plan["plan_id"]
            calls.append("start_run")
            return {"run_id": "release-run:stage1"}

        def get_plan(self, plan_id):
            assert plan_id == plan["plan_id"]
            return deepcopy(plan)

    class CollectboxStore:
        def status(self, *, plan_id):
            assert plan_id == plan["plan_id"]
            return deepcopy(projection)

    def job():
        return {
            "job_id": "oneclick-job:stage1",
            "phase": "PENDING",
            "targets": [
                {"target_label": outcome["target_label"]}
                for outcome in projection["action"]["platforms"][0][
                    "target_outcomes"
                ]
            ]
            + [
                {"target_label": "shopee:GLOBAL"},
                {"target_label": "ozon:RU"},
            ],
        }

    class ControlStore:
        @staticmethod
        def ensure_job(**_kwargs):
            calls.append("ensure_job")
            return job()

        @staticmethod
        def set_dispatch_capability(job_id, *, enabled):
            assert job_id == "oneclick-job:stage1"
            assert enabled is True
            calls.append("set_dispatch_capability")
            return job()

        @staticmethod
        def start_explicit_batch(job_id, *, target_labels):
            assert job_id == "oneclick-job:stage1"
            assert target_labels == tuple(
                outcome["target_label"]
                for outcome in projection["action"]["platforms"][0][
                    "target_outcomes"
                ]
            )
            calls.append("start_tiktok_batch")
            return {
                **job(),
                "batch_scope": "TIKTOK",
                "batch_scope_targets": list(target_labels),
            }

    context = {
        "plan": deepcopy(plan),
        "payload": {"product_revision": plan["product_revision"]},
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
        lambda job_id: calls.append(f"wake:{job_id}"),
    )
    monkeypatch.setattr(
        product_server,
        "_project_oneclick_dispatch_capability",
        lambda value: value,
    )
    return calls


def test_fixture_is_redacted_and_matches_observed_legacy_topology(
    legacy_topology,
):
    assert legacy_topology["schema_version"] == (
        "convergence-stage0-legacy-topology/v1"
    )
    assert legacy_topology["history"] == {
        "collectbox_batch_count": 14,
        "oneclick_job_count": 1,
    }
    assert legacy_topology["source_offer"] == (
        "redacted-production-shaped-offer"
    )
    serialized = json.dumps(legacy_topology, sort_keys=True)
    for forbidden in (
        "confirmation_token",
        "access_token",
        "refresh_token",
        "product_title",
        "description",
        "image_url",
        "external_id",
    ):
        assert forbidden not in serialized


def test_real_http_handler_accepts_observed_tiktok_partial_topology(
    monkeypatch,
    legacy_topology,
    product_http_server,
):
    calls = _install_legacy_read_context(monkeypatch, legacy_topology)
    plan = legacy_topology["approved_plan"]

    status, body = _post(
        product_http_server + "/api/product-workspace/publish-tiktok",
        {
            "confirm_publish": True,
            "plan_id": plan["plan_id"],
            "offer_id": "stage0-redacted-offer",
        },
    )

    assert status == 202
    assert body["accepted"] is True
    assert body["batch_scope"] == "TIKTOK"
    assert body["external_writes_performed"] == []
    assert "start_tiktok_batch" in calls
    assert "wake:oneclick-job:stage1" in calls


def test_five_tiktok_successes_plus_terminal_gb_failure_is_publishable(
    monkeypatch,
    legacy_topology,
):
    _install_legacy_read_context(monkeypatch, legacy_topology)
    plan_id = legacy_topology["approved_plan"]["plan_id"]

    assert product_server._collectbox_platform_succeeded(
        plan_id,
        "TIKTOK",
    ) is True


def _tiktok_partial_row(*, gb_status="FAILED", gb_error=None):
    outcomes = [
        {
            "target_label": label,
            "status": "SUCCEEDED",
            "error_code": None,
            "detail_digest": None,
        }
        for label in (
            "tiktok:LH_PH",
            "tiktok:LH_MY",
            "tiktok:LH_TH",
            "tiktok:LH_VN",
            "tiktok:MX",
        )
    ]
    outcomes.append(
        {
            "target_label": "tiktok:GB",
            "status": gb_status,
            "error_code": gb_error,
            "detail_digest": (
                "1" * 64 if gb_status == "FAILED" else None
            ),
        }
    )
    return {
        "platform": "TIKTOK",
        "status": "RECONCILIATION_REQUIRED",
        "target_outcomes": outcomes,
    }


@pytest.mark.parametrize(
    "platform_status,gb_status,gb_error",
    (
        ("PARTIAL_FAILED", "FAILED", "target_preparation_failed"),
        (
            "RECONCILIATION_REQUIRED",
            "FAILED",
            "approved_detail_readback_mismatch",
        ),
        ("PARTIAL_FAILED", "SUCCEEDED", None),
        ("RECONCILIATION_REQUIRED", "REPAIRED_SUCCEEDED", None),
    ),
)
def test_tiktok_partial_row_accepts_any_structurally_valid_terminal_gb(
    platform_status,
    gb_status,
    gb_error,
):
    row = _tiktok_partial_row(
        gb_status=gb_status,
        gb_error=gb_error,
    )
    row["status"] = platform_status

    assert product_server._collectbox_platform_row_publishable(
        row,
        "TIKTOK",
    ) is True


@pytest.mark.parametrize("gb_status", ("PENDING", "RUNNING"))
def test_tiktok_partial_row_rejects_nonterminal_gb(gb_status):
    row = _tiktok_partial_row(gb_status=gb_status, gb_error=None)

    assert product_server._collectbox_platform_row_publishable(
        row,
        "TIKTOK",
    ) is False


def test_tiktok_partial_row_rejects_missing_gb():
    row = _tiktok_partial_row(
        gb_status="FAILED",
        gb_error="target_preparation_failed",
    )
    row["target_outcomes"] = [
        outcome
        for outcome in row["target_outcomes"]
        if outcome["target_label"] != "tiktok:GB"
    ]

    assert product_server._collectbox_platform_row_publishable(
        row,
        "TIKTOK",
    ) is False


def test_tiktok_partial_row_rejects_duplicate_gb():
    row = _tiktok_partial_row(
        gb_status="FAILED",
        gb_error="target_preparation_failed",
    )
    row["target_outcomes"].append(deepcopy(row["target_outcomes"][-1]))

    assert product_server._collectbox_platform_row_publishable(
        row,
        "TIKTOK",
    ) is False


@pytest.mark.parametrize(
    "mutate",
    (
        lambda outcome: outcome.pop("detail_digest"),
        lambda outcome: outcome.update(detail_digest="not-a-digest"),
        lambda outcome: outcome.update(error_code=None),
        lambda outcome: outcome.update(target_label=" tiktok:GB"),
    ),
)
def test_tiktok_partial_row_rejects_malformed_terminal_gb(mutate):
    row = _tiktok_partial_row(
        gb_status="FAILED",
        gb_error="target_preparation_failed",
    )
    mutate(row["target_outcomes"][-1])

    assert product_server._collectbox_platform_row_publishable(
        row,
        "TIKTOK",
    ) is False


def test_tiktok_partial_row_rejects_any_non_gb_failure():
    row = _tiktok_partial_row(
        gb_status="FAILED",
        gb_error="target_preparation_failed",
    )
    row["target_outcomes"][0] = {
        "target_label": "tiktok:LH_PH",
        "status": "FAILED",
        "error_code": "target_preparation_failed",
        "detail_digest": "2" * 64,
    }

    assert product_server._collectbox_platform_row_publishable(
        row,
        "TIKTOK",
    ) is False


def test_three_platform_http_routes_are_independent(
    monkeypatch,
    product_http_server,
):
    calls = []

    def response(platform):
        def start(payload):
            calls.append((platform, deepcopy(payload)))
            return 202, {
                "ok": True,
                "accepted": True,
                "external_writes_performed": [],
                "job": {"phase": "PENDING", "targets": [platform]},
            }

        return start

    monkeypatch.setattr(
        product_server,
        "_start_tiktok_release",
        response("TIKTOK"),
    )
    monkeypatch.setattr(
        product_server,
        "_start_shopee_global_release",
        response("SHOPEE_GLOBAL"),
    )
    monkeypatch.setattr(
        product_server,
        "_start_ozon_release",
        response("OZON"),
    )

    endpoints = (
        ("/api/product-workspace/publish-tiktok", "TIKTOK"),
        (
            "/api/product-workspace/publish-shopee-global",
            "SHOPEE_GLOBAL",
        ),
        ("/api/product-workspace/publish-ozon", "OZON"),
    )
    for endpoint, expected in endpoints:
        before = len(calls)
        status, body = _post(
            product_http_server + endpoint,
            {"confirm_publish": True, "plan_id": "stage0-plan"},
        )
        assert status == 202
        assert body["accepted"] is True
        assert len(calls) == before + 1
        assert calls[-1][0] == expected

    assert [platform for platform, _payload in calls] == [
        "TIKTOK",
        "SHOPEE_GLOBAL",
        "OZON",
    ]
