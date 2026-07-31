import json
import sqlite3
from http.server import ThreadingHTTPServer
import threading
import urllib.error
import urllib.parse
import urllib.request

import pytest

from modules.products import server as product_server
from shared_platform.collectbox_action import (
    IMPORTED,
    CollectBoxActionStore,
    CollectBoxPlatformResult,
    approved_plan_identity,
)


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
    encoded = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url,
        data=encoded,
        method=method,
        headers={"Content-Type": "application/json"} if encoded else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _plan():
    return {
        "plan_id": "omnichannel:approved",
        "product_id": "3846511157",
        "payload_digest": "a" * 64,
        "targets": ["tiktok:MX", "shopee:MY", "ozon:RU"],
        "status": "APPROVED",
        "approval": {"status": "APPROVED", "approved_by": "Kyle"},
        "confirmation_token": "exact-approved-token",
        "payload": {"product_revision": 31},
    }


def _context():
    plan = _plan()
    return {
        "plan": plan,
        "payload": {"product_revision": 31},
        "dashboard": {
            "_source_identity_inputs": {
                "collect_box": {},
            },
        },
        "store": object(),
    }


def _post_body():
    identity = approved_plan_identity(_plan())
    return {
        "offer_id": identity["offer_id"],
        "plan_id": identity["plan_id"],
        "product_revision": identity["product_revision"],
        "payload_digest": identity["payload_digest"],
        "targets_digest": identity["targets_digest"],
        "confirmation_token": "exact-approved-token",
        "approved_by": "Kyle",
        "confirm_collectbox_action": True,
    }


def test_server_derives_collectbox_id_and_rejects_client_override(
    monkeypatch,
    tmp_path,
):
    store = CollectBoxActionStore(tmp_path / "platform.db")
    context = _context()
    captured = []
    clock = [100.0]

    monkeypatch.setattr(
        product_server,
        "_oneclick_approved_context",
        lambda _data, **_kwargs: (context, None),
    )
    monkeypatch.setattr(
        product_server,
        "_collectbox_action_store",
        lambda: store,
    )
    monkeypatch.setattr(
        product_server,
        "_collectbox_action_timing",
        lambda: (
            lambda: clock[0],
            lambda seconds: clock.__setitem__(0, clock[0] + seconds),
        ),
    )

    def adapter(request):
        captured.append(request)
        return CollectBoxPlatformResult(
            status="SUCCEEDED",
            outcome=IMPORTED,
            platform_detail_id=(9001 if request.platform == "TIKTOK" else 7002),
            external_writes=(
                "miaoshou:collectbox:claim:"
                + request.platform.casefold(),
            ),
            external_write_count=1,
            receipt_evidence={"checks": {"claim_accepted": True}},
        )

    monkeypatch.setattr(
        product_server,
        "_collectbox_platform_adapter",
        lambda: adapter,
    )

    preview_status, preview = product_server._preview_collectbox_action(
        {
            "offer_id": _plan()["product_id"],
            "plan_id": _plan()["plan_id"],
        }
    )
    assert preview_status == 200
    assert preview["action"]["status"] == "READY"
    assert _plan()["product_id"] not in json.dumps(preview)

    rejected_status, rejected = product_server._start_collectbox_action(
        {
            **_post_body(),
            "commonCollectBoxDetailId": "999999",
        }
    )
    assert rejected_status == 400
    assert rejected["error"]["code"] == "client_collectbox_override_forbidden"
    assert captured == []

    status, response = product_server._start_collectbox_action(_post_body())
    assert status == 200
    assert response["action"]["status"] == "SUCCEEDED"
    assert [request.platform for request in captured] == [
        "TIKTOK",
        "SHOPEE",
    ]
    assert all(
        request.common_collect_box_detail_id == _plan()["product_id"]
        for request in captured
    )
    assert _plan()["product_id"] not in json.dumps(response)


def test_collectbox_optional_review_package_identity_conflict_blocks_start(
    monkeypatch,
    tmp_path,
):
    context = _context()
    context["dashboard"]["_source_identity_inputs"]["collect_box"] = {
        "detail_id": "999999"
    }
    monkeypatch.setattr(
        product_server,
        "_oneclick_approved_context",
        lambda _data, **_kwargs: (context, None),
    )
    store = CollectBoxActionStore(tmp_path / "platform.db")
    monkeypatch.setattr(product_server, "_collectbox_action_store", lambda: store)

    status, response = product_server._start_collectbox_action(_post_body())

    assert status == 409
    assert response["error"]["code"] == "collectbox_source_identity_unavailable"
    assert store.status(plan_id=_plan()["plan_id"]) is None


def test_step1_collectbox_is_sole_canonical_action_and_publish_fails_closed(
    monkeypatch,
):
    plan = _plan()
    monkeypatch.setattr(
        product_server,
        "_oneclick_control_store",
        lambda: type(
            "LegacyStore",
            (),
            {
                "get_job": staticmethod(
                    lambda **_kwargs: {
                        "phase": "READY",
                        "runnable_target_count": 3,
                    }
                )
            },
        )(),
    )
    monkeypatch.setattr(
        product_server,
        "_collectbox_action_store",
        lambda: type(
            "CollectStore",
            (),
            {"status": staticmethod(lambda **_kwargs: None)},
        )(),
    )

    projected = product_server._apply_oneclick_release_authority(
        {
            "plan": plan,
            "plan_approved": True,
            "publish_ready": True,
        }
    )
    assert projected["publish_ready"] is False
    assert projected["runnable_target_count"] == 0
    assert projected["target_recovery_actions"] == []
    assert projected["canonical_next_action"] == {
        "action": "start_collectbox_action",
        "target_focus": None,
    }
    assert projected["plan"]["targets_digest"] == approved_plan_identity(plan)[
        "targets_digest"
    ]
    assert set(projected["collectbox_action"]["approved_plan"]) == {
        "plan_id",
        "product_revision",
        "payload_digest",
        "targets_digest",
    }
    assert len(projected["collectbox_action"]["action"]["platforms"]) == 2

    context = _context()
    monkeypatch.setattr(
        product_server,
        "_oneclick_approved_context",
        lambda _data, **_kwargs: (context, None),
    )
    status, response = product_server._start_oneclick_release(
        {
            "confirm_publish": True,
            "offer_id": plan["product_id"],
            "plan_id": plan["plan_id"],
        }
    )
    assert status == 409
    assert response["error"]["code"] == "step1_collectbox_required"


def test_collectbox_status_missing_is_redacted_not_found(monkeypatch):
    monkeypatch.setattr(
        product_server,
        "_oneclick_approved_context",
        lambda _data, **_kwargs: (_context(), None),
    )
    monkeypatch.setattr(
        product_server,
        "_collectbox_action_store",
        lambda: type(
            "CollectStore",
            (),
            {"status": staticmethod(lambda **_kwargs: None)},
        )(),
    )

    status, response = product_server._collectbox_action_status(
        {"offer_id": "3846511157", "plan_id": "omnichannel:approved"}
    )

    assert status == 404
    assert response["error"]["category"] == "NOT_FOUND"
    assert response["error"]["code"] == "collectbox_action_not_found"
    assert len(response["error"]["detail_digest"]) == 64


def test_collectbox_status_compares_the_same_public_plan_identity(monkeypatch):
    context = _context()
    identity = approved_plan_identity(_plan())
    public_identity = {
        key: identity[key]
        for key in (
            "plan_id",
            "product_revision",
            "payload_digest",
            "targets_digest",
        )
    }
    persisted = {
        "schema_version": "collectbox-action-status/v1",
        "ok": True,
        "persisted": True,
        "approved_plan": public_identity,
        "action": {
            "action_id": "collectbox-action:fixture",
            "status": "PARTIAL_FAILED",
            "start_allowed": False,
            "retry_allowed": False,
            "terminal": True,
            "platforms": [],
            "error": None,
        },
        "external_writes_performed": [
            "miaoshou:collectbox:claim:tiktok",
        ],
        "external_write_count": 1,
        "canonical_next_action": None,
    }
    monkeypatch.setattr(
        product_server,
        "_oneclick_approved_context",
        lambda _data, **_kwargs: (context, None),
    )
    monkeypatch.setattr(
        product_server,
        "_collectbox_action_store",
        lambda: type(
            "CollectStore",
            (object,),
            {"status": staticmethod(lambda **_kwargs: persisted)},
        )(),
    )

    status, response = product_server._collectbox_action_status(
        {"offer_id": _plan()["product_id"], "plan_id": _plan()["plan_id"]}
    )

    assert status == 200
    assert response is persisted


def test_collectbox_http_routes_use_direct_frozen_schema(
    monkeypatch,
    product_http_server,
):
    seen = []
    ready = {
        "schema_version": "collectbox-action-status/v1",
        "ok": True,
        "persisted": False,
        "approved_plan": {
            "plan_id": _plan()["plan_id"],
            "product_revision": 31,
            "payload_digest": "a" * 64,
            "targets_digest": approved_plan_identity(_plan())["targets_digest"],
        },
        "action": {"status": "READY", "platforms": []},
        "external_writes_performed": [],
        "external_write_count": 0,
        "canonical_next_action": {
            "action": "start_collectbox_action",
            "target_focus": None,
        },
    }

    def preview(data):
        seen.append(("preview", data))
        return 200, ready

    def status(data):
        seen.append(("status", data))
        return 404, {**ready, "ok": False}

    def start(data):
        seen.append(("start", data))
        return 200, {**ready, "persisted": True}

    monkeypatch.setattr(product_server, "_preview_collectbox_action", preview)
    monkeypatch.setattr(product_server, "_collectbox_action_status", status)
    monkeypatch.setattr(product_server, "_start_collectbox_action", start)
    query = urllib.parse.urlencode(
        {"offer_id": _plan()["product_id"], "plan_id": _plan()["plan_id"]}
    )

    preview_code, preview_body = _request(
        product_http_server
        + "/api/product-workspace/collectbox-action/preview?"
        + query
    )
    status_code, status_body = _request(
        product_http_server
        + "/api/product-workspace/collectbox-action/status?"
        + query
    )
    start_code, start_body = _request(
        product_http_server + "/api/product-workspace/collectbox-action/start",
        method="POST",
        payload=_post_body(),
    )

    assert (preview_code, status_code, start_code) == (200, 404, 200)
    assert preview_body["schema_version"] == "collectbox-action-status/v1"
    assert status_body["schema_version"] == "collectbox-action-status/v1"
    assert start_body["schema_version"] == "collectbox-action-status/v1"
    assert [name for name, _data in seen] == ["preview", "status", "start"]


def test_malformed_approved_plan_projects_blocked_identity_not_dashboard_crash(
    monkeypatch,
):
    malformed = {**_plan(), "payload": {}, "product_revision": 31}
    monkeypatch.setattr(
        product_server,
        "_collectbox_action_store",
        lambda: type("Store", (), {"status": staticmethod(lambda **_kw: None)})(),
    )

    projected = product_server._apply_oneclick_release_authority(
        {"plan": malformed, "plan_approved": True}
    )

    assert projected["collectbox_action"]["action"]["status"] == "BLOCKED_IDENTITY"
    assert projected["collectbox_action"]["action"]["error"]["code"] == (
        "collectbox_approved_plan_identity_invalid"
    )
    assert projected["canonical_next_action"] is None


def test_dashboard_authority_get_is_pure_on_release_db_without_new_tables(
    monkeypatch,
    tmp_path,
):
    path = tmp_path / "release.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE existing_release_state (value TEXT)")
        connection.execute("INSERT INTO existing_release_state VALUES ('kept')")
    before = path.read_bytes()
    store = CollectBoxActionStore(path)
    monkeypatch.setattr(product_server, "_collectbox_action_store", lambda: store)

    projected = product_server._apply_oneclick_release_authority(
        {"plan": _plan(), "plan_approved": True}
    )

    assert projected["collectbox_action"]["action"]["status"] == "READY"
    assert path.read_bytes() == before
    with sqlite3.connect(path) as connection:
        assert [
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            )
        ] == ["existing_release_state"]
