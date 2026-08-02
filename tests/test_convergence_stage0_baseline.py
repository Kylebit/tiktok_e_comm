"""Stage-0 characterization gates for the legacy one-click topology.

These tests deliberately do not repair the known defect.  They preserve the
production-shaped state that caused the 2026-08-02 HTTP 409, prove the real
HTTP handler returns the server error code, and freeze platform routing while
the simplified successor design is reviewed.
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

    class ReleaseStore:
        def start_run(self, _plan_id):
            pytest.fail("the collect-box gate must run before a canonical run")

        def get_plan(self, _plan_id):
            return deepcopy(plan)

    class CollectboxStore:
        def status(self, *, plan_id):
            assert plan_id == plan["plan_id"]
            return deepcopy(projection)

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
    assert legacy_topology["source_offer"] != "3846511157"
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


def test_real_http_handler_reproduces_tiktok_409_and_exact_server_code(
    monkeypatch,
    legacy_topology,
    product_http_server,
):
    _install_legacy_read_context(monkeypatch, legacy_topology)
    plan = legacy_topology["approved_plan"]

    status, body = _post(
        product_http_server + "/api/product-workspace/publish-tiktok",
        {
            "confirm_publish": True,
            "plan_id": plan["plan_id"],
            "offer_id": "stage0-redacted-offer",
        },
    )

    assert status == 409
    assert body["error"]["code"] == "step1_collectbox_required"
    assert body["canonical_next_action"]["action"] == (
        "start_collectbox_action"
    )
    assert body["external_writes_performed"] == []


@pytest.mark.xfail(
    strict=True,
    reason=(
        "known stage-0 defect: the current GB preparation failure is not "
        "covered by the previously approved non-blocking GB policy"
    ),
)
def test_known_gap_five_tiktok_successes_plus_gb_failure_is_publishable(
    monkeypatch,
    legacy_topology,
):
    _install_legacy_read_context(monkeypatch, legacy_topology)
    plan_id = legacy_topology["approved_plan"]["plan_id"]

    assert product_server._collectbox_platform_succeeded(
        plan_id,
        "TIKTOK",
    ) is True


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
