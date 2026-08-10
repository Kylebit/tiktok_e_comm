from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
from threading import Thread
import urllib.error
import urllib.parse
import urllib.request

import pytest

from modules.products import server as product_server
from shared_platform.product_publication_reports import ProductPublicationReportStore
from shared_platform.product_publication_runs import ProductPublicationRunStore


def _payload() -> dict:
    return {
        "schema_version": "product-publication-report/v1",
        "report_id": "publication-report:http-run",
        "run_id": "http-run",
        "offer_id": "3838616043",
        "revision": 31,
        "plan_id": "omnichannel:" + "c" * 64,
        "snapshot": {
            "schema_version": "approved-publication-snapshot/v4",
            "digest": "d" * 64,
        },
        "status": "PROCESSING",
        "summary": {
            "schema_version": "product-publication-summary/v1",
            "overall_status": "PROCESSING",
            "platforms": [
                {
                    "platform": "SHOPEE",
                    "status": "PROCESSING",
                    "target_count": 4,
                    "verified_count": 0,
                    "processing_count": 4,
                    "failed_count": 0,
                }
            ],
            "evidence": {
                "snapshot_verified": True,
                "dispatch_attempted": True,
                "readback_completed": False,
                "external_write_count": 4,
            },
            "requires_human_action": True,
        },
    }


@pytest.fixture
def report_http_server(tmp_path, monkeypatch):
    store = ProductPublicationReportStore(
        tmp_path / "orbit_platform.db",
        reports_root=tmp_path / "reports" / "product-publication",
    )
    run_store = ProductPublicationRunStore(tmp_path / "orbit_platform.db")
    store.store_report(_payload())
    monkeypatch.setattr(
        product_server,
        "_product_publication_report_store",
        lambda: store,
    )
    monkeypatch.setattr(
        product_server,
        "_product_publication_run_store",
        lambda: run_store,
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), product_server.Handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", store, run_store
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _get(base_url: str, path: str, query: dict[str, object]) -> tuple[int, dict]:
    url = base_url + path + "?" + urllib.parse.urlencode(query)
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_http_gets_report_and_list_without_mutating_store(report_http_server):
    base_url, store, _run_store = report_http_server
    before_db = store.path.read_bytes()
    before_report = (store.reports_root / "3838616043/31/http-run/report.json").read_bytes()

    status, single = _get(
        base_url,
        "/api/product-workspace/publication-report",
        {"offer_id": "3838616043", "report_id": "publication-report:http-run"},
    )
    list_status, listing = _get(
        base_url,
        "/api/product-workspace/publication-reports",
        {"offer_id": "3838616043", "revision": 31},
    )

    assert status == 200
    assert list_status == 200
    assert single["report"]["status"] == "PROCESSING"
    assert single["report"]["summary"]["evidence"]["readback_completed"] is False
    assert listing["latest"]["report_id"] == "publication-report:http-run"
    assert listing["count"] == 1
    encoded = json.dumps({"single": single, "listing": listing})
    for forbidden in (
        "token",
        "raw_response",
        "description",
        "http-run/report.json",
        "external_id",
    ):
        assert forbidden not in encoded
    assert store.path.read_bytes() == before_db
    assert (store.reports_root / "3838616043/31/http-run/report.json").read_bytes() == before_report


def test_http_requires_offer_scope_and_rejects_cross_offer(report_http_server):
    base_url, _store, _run_store = report_http_server
    missing_status, _ = _get(
        base_url,
        "/api/product-workspace/publication-report",
        {"report_id": "publication-report:http-run"},
    )
    cross_status, cross = _get(
        base_url,
        "/api/product-workspace/publication-report",
        {"offer_id": "9999999999", "report_id": "publication-report:http-run"},
    )
    bad_revision, _ = _get(
        base_url,
        "/api/product-workspace/publication-reports",
        {"offer_id": "3838616043", "revision": "../31"},
    )

    assert missing_status == 400
    assert cross_status == 404
    assert cross == {"ok": False, "error": "publication report not found"}
    assert bad_revision == 400


def test_post_routes_are_not_added(report_http_server):
    base_url, _store, _run_store = report_http_server
    request = urllib.request.Request(
        base_url + "/api/product-workspace/publication-report",
        data=b"{}",
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as raised:
        urllib.request.urlopen(request, timeout=10)
    assert raised.value.code in {404, 405}


def test_http_get_projects_durable_running_job_as_processing(report_http_server):
    base_url, _report_store, run_store = report_http_server
    created = run_store.create_run(
        run_id="http-async-run",
        offer_id="3838616043",
        revision=32,
        plan_id="omnichannel:" + "e" * 64,
        snapshot_digest="sha256:" + "f" * 64,
        platform_scope=("OZON",),
        target_count=1,
    )
    run_store.mark_running(run_id=created.run_id)

    status, body = _get(
        base_url,
        "/api/product-workspace/publication-report",
        {"offer_id": "3838616043", "report_id": created.report_id},
    )

    assert status == 200
    assert body["report"]["schema_version"] == "product-publication-run-status/v1"
    assert body["report"]["status"] == "PROCESSING"
    assert body["report"]["summary"]["evidence"]["dispatch_attempted"] is None
