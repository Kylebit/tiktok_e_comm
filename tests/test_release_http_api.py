from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
from threading import Thread
import urllib.error
import urllib.request

import pytest

from modules.finance import sku_profit_service
from modules.finance import settlement_report
from modules.products.server import Handler
from shared_platform import release_control


@pytest.fixture
def local_api():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _get_json(url: str) -> tuple[int, dict]:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_release_dashboard_route_returns_read_only_payload(local_api, monkeypatch):
    monkeypatch.setattr(
        release_control,
        "build_release_dashboard",
        lambda **kwargs: {
            "ok": True,
            "mode": "rehearsal",
            "safety": {"external_writes_performed": []},
            "received": kwargs,
        },
    )

    status, payload = _get_json(
        local_api + "/api/release/dashboard?offer_id=3828811808&seller_sku=0946"
    )

    assert status == 200
    assert payload["mode"] == "rehearsal"
    assert payload["safety"]["external_writes_performed"] == []
    assert payload["received"]["seller_sku"] == "0946"


def test_weekly_route_accepts_complete_week_and_rejects_other_periods(
    local_api, monkeypatch
):
    monkeypatch.setattr(
        release_control,
        "build_weekly_profit_rehearsal",
        lambda **kwargs: {
            "ok": True,
            "persisted": False,
            "notifications_sent": False,
            "period_start": kwargs["period_start"].isoformat(),
        },
    )
    status, payload = _get_json(
        local_api + "/api/release/weekly-preview?start=2026-07-13&end=2026-07-19"
    )
    assert status == 200
    assert payload["persisted"] is False

    # Restore the real validator. It rejects before reading any source files.
    monkeypatch.undo()
    for start, end in (
        ("2026-07-13", "2026-07-20"),
        ("2026-07-14", "2026-07-20"),
    ):
        status, payload = _get_json(
            local_api + f"/api/release/weekly-preview?start={start}&end={end}"
        )
        assert status == 400
        assert "Monday-through-Sunday" in payload["error"]


def test_sku_route_uses_explicit_percent_and_maps_validation_to_400(
    local_api, monkeypatch
):
    captured = {}

    def fake_estimate(sku, **kwargs):
        captured.update({"sku": sku, **kwargs})
        return {"ok": True, "sku": sku, "compare": []}

    monkeypatch.setattr(sku_profit_service, "estimate", fake_estimate)
    status, payload = _get_json(
        local_api
        + "/api/sku-profit?sku=0021&platform=both&ad_rate_percent=1&lookback_days=14"
    )
    assert status == 200
    assert payload["ok"] is True
    assert captured["ad_rate"] is None
    assert captured["ad_rate_percent"] == 1

    monkeypatch.undo()
    status, payload = _get_json(
        local_api + "/api/sku-profit?sku=0021&platform=amazon"
    )
    assert status == 400
    assert "platform" in payload["error"]


def test_unknown_exact_platform_id_is_an_http_404(local_api, monkeypatch):
    monkeypatch.setattr(
        sku_profit_service,
        "estimate",
        lambda sku, **kwargs: {
            "ok": False,
            "sku": sku,
            "partial": False,
            "platforms": {},
            "error": "exact platform SKU not found",
        },
    )

    status, payload = _get_json(
        local_api + "/api/sku-profit?sku=9999999999990021&platform=both"
    )

    assert status == 404
    assert payload["ok"] is False


def test_settlement_order_detail_passes_explicit_ad_rate_percent(
    local_api, monkeypatch
):
    captured = {}

    def fake_orders(filename, **kwargs):
        captured.update({"filename": filename, **kwargs})
        return {"rows": []}

    monkeypatch.setattr(settlement_report, "order_rows_for_file", fake_orders)
    status, payload = _get_json(
        local_api
        + "/api/settlement/orders?file=fixture.csv&rate=0.22&ad_rate_percent=37"
    )

    assert status == 200
    assert payload["ok"] is True
    assert captured["ad_rate_pct"] == 37

    status, payload = _get_json(
        local_api
        + "/api/settlement/orders?file=fixture.csv&ad_rate=22&ad_rate_percent=37"
    )
    assert status == 400
    assert "not both" in payload["error"]
