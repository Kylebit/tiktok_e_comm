from __future__ import annotations

import json
from copy import deepcopy
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Event, Thread
from time import monotonic, sleep
import urllib.error
import urllib.parse
import urllib.request

import pytest

from domains.product_operations import build_approved_publication_snapshot
from modules.products import server as product_server
from shared_platform.product_publication_reports import ProductPublicationReportStore
from shared_platform.product_publication_runs import ProductPublicationRunStore
from test_approved_publication_snapshot import _approved_plan


class _SnapshotStore:
    def __init__(self, snapshot: dict) -> None:
        self.snapshot = deepcopy(snapshot)

    def approved_publication_snapshot(self, **_kwargs):
        return deepcopy(self.snapshot)


def _result(request, status: str = "PUBLISHED") -> dict:
    return {
        "schema_version": "product-publication-platform-result/v1",
        "platform": request.platform,
        "targets": [
            {"target_label": target, "status": status}
            for target in request.target_labels
        ],
        "dispatch_attempted": True,
        "readback_completed": True,
        "external_write_count": len(request.target_labels),
        "requires_human_action": status == "FAILED",
    }


@pytest.fixture
def publication_start_server(tmp_path: Path, monkeypatch):
    snapshot = build_approved_publication_snapshot(_approved_plan()).payload()
    report_store = ProductPublicationReportStore(
        tmp_path / "orbit_platform.db",
        reports_root=tmp_path / "reports" / "product-publication",
    )
    run_store = ProductPublicationRunStore(tmp_path / "orbit_platform.db")
    calls: list[object] = []

    def executor(request):
        calls.append(request)
        return _result(request)

    monkeypatch.setattr(product_server, "_release_store", lambda: _SnapshotStore(snapshot))
    monkeypatch.setattr(
        product_server, "_product_publication_report_store", lambda: report_store
    )
    monkeypatch.setattr(
        product_server, "_product_publication_run_store", lambda: run_store
    )
    monkeypatch.setattr(
        product_server,
        "_product_publication_platform_executors",
        lambda: {"TIKTOK": executor, "SHOPEE": executor, "OZON": executor},
    )
    for legacy_name in (
        "_start_tiktok_release",
        "_start_shopee_global_release",
        "_start_ozon_release",
    ):
        monkeypatch.setattr(
            product_server,
            legacy_name,
            lambda _data, name=legacy_name: pytest.fail(f"legacy route called: {name}"),
        )

    server = ThreadingHTTPServer(("127.0.0.1", 0), product_server.Handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (
            f"http://127.0.0.1:{server.server_address[1]}",
            snapshot,
            report_store,
            calls,
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _post(base_url: str, path: str, body: dict) -> tuple[int, dict]:
    request = urllib.request.Request(
        base_url + path,
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def _get(base_url: str, path: str, query: dict[str, str]) -> tuple[int, dict]:
    url = base_url + path + "?" + urllib.parse.urlencode(query)
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.status, json.loads(response.read())


def _wait_for_report(report_store, *, report_id: str, offer_id: str, timeout=3):
    deadline = monotonic() + timeout
    while monotonic() < deadline:
        report = report_store.get_report(report_id=report_id, offer_id=offer_id)
        if report is not None:
            return report
        sleep(0.01)
    raise AssertionError("publication report did not reach a terminal fact")


@pytest.mark.parametrize(
    ("path", "platform"),
    [
        ("/api/product-workspace/publish-tiktok", "TIKTOK"),
        ("/api/product-workspace/publish-shopee-global", "SHOPEE"),
        ("/api/product-workspace/publish-ozon", "OZON"),
    ],
)
def test_each_product_center_button_starts_exactly_one_runner_platform(
    publication_start_server, path, platform
):
    base_url, snapshot, report_store, calls = publication_start_server

    status, body = _post(
        base_url,
        path,
        {
            "offer_id": snapshot["offer_id"],
            "plan_id": snapshot["plan_id"],
            "platform": "ATTACKER_CONTROLLED",
            "run_id": "attacker-run",
            "platform_executors": {"OZON": "not-callable"},
        },
    )

    assert status == 202
    assert body == {
        "ok": True,
        "schema_version": "product-publication-start/v1",
        "platform": platform,
        "report_id": body["report_id"],
        "run_id": body["run_id"],
    }
    assert body["report_id"] == f"publication-report:{body['run_id']}"
    assert body["run_id"] != "attacker-run"
    persisted = _wait_for_report(
        report_store,
        report_id=body["report_id"], offer_id=snapshot["offer_id"]
    )
    assert len(calls) == 1
    assert calls[0].platform == platform
    assert persisted["status"] == "PUBLISHED"
    assert [row["platform"] for row in persisted["summary"]["platforms"]] == [
        platform
    ]
    get_status, get_body = _get(
        base_url,
        "/api/product-workspace/publication-report",
        {"offer_id": snapshot["offer_id"], "report_id": body["report_id"]},
    )
    assert get_status == 200
    assert get_body["report"]["run_id"] == body["run_id"]
    assert get_body["report"]["status"] == "PUBLISHED"


def test_unavailable_server_executor_persists_failed_report(tmp_path, monkeypatch):
    snapshot = build_approved_publication_snapshot(_approved_plan()).payload()
    report_store = ProductPublicationReportStore(
        tmp_path / "orbit_platform.db",
        reports_root=tmp_path / "reports" / "product-publication",
    )
    run_store = ProductPublicationRunStore(tmp_path / "orbit_platform.db")
    monkeypatch.setattr(product_server, "_release_store", lambda: _SnapshotStore(snapshot))
    monkeypatch.setattr(
        product_server, "_product_publication_report_store", lambda: report_store
    )
    monkeypatch.setattr(
        product_server, "_product_publication_run_store", lambda: run_store
    )
    monkeypatch.setattr(
        product_server, "_product_publication_platform_executors", lambda: {}
    )

    status, body = product_server._start_product_publication(
        {"offer_id": snapshot["offer_id"], "plan_id": snapshot["plan_id"]},
        platform="SHOPEE",
    )

    assert status == 202
    report = _wait_for_report(
        report_store,
        report_id=body["report_id"],
        offer_id=snapshot["offer_id"],
    )
    assert report["status"] == "FAILED"
    assert report["summary"]["platforms"][0]["status"] == "FAILED"
    assert report["summary"]["evidence"] == {
        "snapshot_verified": True,
        "dispatch_attempted": False,
        "readback_completed": False,
        "external_write_count": 0,
    }


def test_start_returns_before_provider_work_finishes(tmp_path, monkeypatch):
    """The HTTP start seam must not inherit provider latency.

    The caller runs in a test thread so the baseline can be released after the
    assertion without hanging the suite.
    """

    snapshot = build_approved_publication_snapshot(_approved_plan()).payload()
    report_store = ProductPublicationReportStore(
        tmp_path / "orbit_platform.db",
        reports_root=tmp_path / "reports" / "product-publication",
    )
    run_store = ProductPublicationRunStore(tmp_path / "orbit_platform.db")
    release_executor = Event()
    entered_executor = Event()

    def slow_executor(request):
        entered_executor.set()
        assert release_executor.wait(timeout=2)
        return _result(request)

    monkeypatch.setattr(product_server, "_release_store", lambda: _SnapshotStore(snapshot))
    monkeypatch.setattr(
        product_server, "_product_publication_report_store", lambda: report_store
    )
    monkeypatch.setattr(
        product_server, "_product_publication_run_store", lambda: run_store
    )
    monkeypatch.setattr(
        product_server,
        "_product_publication_platform_executors",
        lambda: {"TIKTOK": slow_executor},
    )

    result: dict[str, object] = {}

    def invoke_start():
        result["value"] = product_server._start_product_publication(
            {"offer_id": snapshot["offer_id"], "plan_id": snapshot["plan_id"]},
            platform="TIKTOK",
        )

    caller = Thread(target=invoke_start, daemon=True)
    caller.start()
    assert entered_executor.wait(timeout=1)
    caller.join(timeout=0.1)
    returned_before_provider = not caller.is_alive()
    try:
        if returned_before_provider:
            status, body = result["value"]
            report_before_provider = report_store.get_report(
                report_id=body["report_id"], offer_id=snapshot["offer_id"]
            )
        else:
            status = body = report_before_provider = None
    finally:
        release_executor.set()
        caller.join(timeout=2)

    assert returned_before_provider is True
    assert status == 202
    assert report_before_provider is None
    _wait_for_report(
        report_store,
        report_id=body["report_id"],
        offer_id=snapshot["offer_id"],
    )


def test_worker_launch_failure_is_durable_failed_without_platform_call(
    tmp_path, monkeypatch
):
    snapshot = build_approved_publication_snapshot(_approved_plan()).payload()
    report_store = ProductPublicationReportStore(
        tmp_path / "orbit_platform.db",
        reports_root=tmp_path / "reports" / "product-publication",
    )
    run_store = ProductPublicationRunStore(tmp_path / "orbit_platform.db")
    monkeypatch.setattr(product_server, "_release_store", lambda: _SnapshotStore(snapshot))
    monkeypatch.setattr(
        product_server, "_product_publication_report_store", lambda: report_store
    )
    monkeypatch.setattr(
        product_server, "_product_publication_run_store", lambda: run_store
    )
    monkeypatch.setattr(
        product_server,
        "_product_publication_platform_executors",
        lambda: {"OZON": lambda _request: pytest.fail("executor must not run")},
    )
    monkeypatch.setattr(
        product_server,
        "_launch_product_publication_background",
        lambda _callback: (_ for _ in ()).throw(RuntimeError("thread unavailable")),
    )

    status, body = product_server._start_product_publication(
        {"offer_id": snapshot["offer_id"], "plan_id": snapshot["plan_id"]},
        platform="OZON",
    )

    assert status == 202
    run = run_store.get_run(
        report_id=body["report_id"], offer_id=snapshot["offer_id"]
    )
    assert run["state"] == "FAILED"
    assert run["failure_code"] == "WORKER_LAUNCH_FAILED"
    assert report_store.get_report(
        report_id=body["report_id"], offer_id=snapshot["offer_id"]
    ) is None


def test_execution_identity_drift_fails_before_running_or_dispatch(tmp_path, monkeypatch):
    snapshot = build_approved_publication_snapshot(_approved_plan()).payload()
    db_path = tmp_path / "orbit_platform.db"
    report_store = ProductPublicationReportStore(
        db_path, reports_root=tmp_path / "reports" / "product-publication"
    )
    run_store = ProductPublicationRunStore(db_path)
    calls = []
    expected = {"skill_digest": "1" * 64, "git_commit": "2" * 40, "code_digest": "3" * 64}
    current = {**expected, "code_digest": "4" * 64}
    created = run_store.create_run(
        run_id="identity-drift-run",
        offer_id=snapshot["offer_id"],
        revision=snapshot["product_revision"],
        plan_id=snapshot["plan_id"],
        snapshot_digest=snapshot["snapshot_digest"],
        platform_scope=("TIKTOK",),
        target_count=2,
        execution_identity=expected,
    )
    monkeypatch.setattr(product_server, "_product_publication_execution_identity", lambda _platform: current)

    product_server._execute_product_publication_background(
        run_id=created.run_id,
        offer_id=snapshot["offer_id"],
        snapshot_digest=snapshot["snapshot_digest"],
        platform="TIKTOK",
        executor=lambda request: calls.append(request),
        release_store=_SnapshotStore(snapshot),
        report_store=report_store,
        run_store=run_store,
        expected_execution_identity=expected,
    )

    run = run_store.get_run_by_id(run_id=created.run_id)
    assert run["state"] == "FAILED"
    assert run["failure_code"] == "EXECUTION_IDENTITY_DRIFT"
    assert run["event_count"] == 2
    assert calls == []
    assert report_store.get_report_by_run(run_id=created.run_id) is None


def test_invalid_frozen_identity_fails_before_run_or_platform(tmp_path, monkeypatch):
    snapshot = build_approved_publication_snapshot(_approved_plan()).payload()
    db_path = tmp_path / "orbit_platform.db"
    run_store = ProductPublicationRunStore(db_path)
    monkeypatch.setattr(product_server, "_release_store", lambda: _SnapshotStore(snapshot))
    monkeypatch.setattr(
        product_server,
        "_product_publication_report_store",
        lambda: ProductPublicationReportStore(
            db_path,
            reports_root=tmp_path / "reports" / "product-publication",
        ),
    )
    monkeypatch.setattr(
        product_server, "_product_publication_run_store", lambda: run_store
    )
    monkeypatch.setattr(
        product_server,
        "_product_publication_platform_executors",
        lambda: {"SHOPEE": lambda _request: pytest.fail("executor must not run")},
    )

    status, body = product_server._start_product_publication(
        {"offer_id": snapshot["offer_id"], "plan_id": "omnichannel:wrong"},
        platform="SHOPEE",
    )

    assert status == 409
    assert body["ok"] is False
    assert "plan identity conflicts" in body["error"]
    assert db_path.exists() is False


def test_product_center_script_polls_report_and_uses_only_public_report_states():
    script = Path("web/static/product_workspace.js").read_text(encoding="utf-8")

    assert "/api/product-workspace/publication-report?" in script
    assert "report_id" in script
    assert "run_id" in script
    assert "PUBLICATION_REPORT_STATUSES" in script
    for status in ("PUBLISHED", "PROCESSING", "PARTIAL", "FAILED"):
        assert f'"{status}"' in script
    platform_state = script[
        script.index("const platformPublish = {"):
        script.index("const platformPublishNames = {")
    ]
    renderer = script[
        script.index("function renderOneClickExecution("):
        script.index("function focusOneClickTarget(")
    ]
    for forbidden in ("IDLE", "PUBLISHING", "SUCCEEDED"):
        assert forbidden not in platform_state
        assert forbidden not in renderer
