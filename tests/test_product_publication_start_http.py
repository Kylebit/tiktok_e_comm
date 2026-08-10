from __future__ import annotations

import json
from copy import deepcopy
from http.server import ThreadingHTTPServer
from pathlib import Path
from threading import Thread
import urllib.error
import urllib.parse
import urllib.request

import pytest

from domains.product_operations import build_approved_publication_snapshot
from modules.products import server as product_server
from shared_platform.product_publication_reports import ProductPublicationReportStore
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
    calls: list[object] = []

    def executor(request):
        calls.append(request)
        return _result(request)

    monkeypatch.setattr(product_server, "_release_store", lambda: _SnapshotStore(snapshot))
    monkeypatch.setattr(
        product_server,
        "_product_publication_report_store",
        lambda: report_store,
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
    assert len(calls) == 1
    assert calls[0].platform == platform
    persisted = report_store.get_report(
        report_id=body["report_id"], offer_id=snapshot["offer_id"]
    )
    assert persisted is not None
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
    monkeypatch.setattr(product_server, "_release_store", lambda: _SnapshotStore(snapshot))
    monkeypatch.setattr(
        product_server, "_product_publication_report_store", lambda: report_store
    )
    monkeypatch.setattr(
        product_server, "_product_publication_platform_executors", lambda: {}
    )

    status, body = product_server._start_product_publication(
        {"offer_id": snapshot["offer_id"], "plan_id": snapshot["plan_id"]},
        platform="SHOPEE",
    )

    assert status == 202
    report = report_store.get_report(
        report_id=body["report_id"], offer_id=snapshot["offer_id"]
    )
    assert report is not None
    assert report["status"] == "FAILED"
    assert report["summary"]["platforms"][0]["status"] == "FAILED"
    assert report["summary"]["evidence"] == {
        "snapshot_verified": True,
        "dispatch_attempted": False,
        "readback_completed": False,
        "external_write_count": 0,
    }


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
