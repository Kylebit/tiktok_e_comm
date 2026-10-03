"""Exact new legacy asset admission without broadening the static boundary."""
from pathlib import Path

from test_audited_supply_snapshot import snapshot, snapshot_http

ROOT = Path(__file__).resolve().parents[1]
SENTINEL = b"owned route boundary sentinel; never production data"


def legacy(snapshot, snapshot_http, monkeypatch):
    server, get = snapshot_http
    monkeypatch.setattr(server, "supply_chain_capture", None)
    return snapshot[0] / "domains/supply_chain_operations/dashboard", get


def test_exact_load_errors_route_preserves_bytes_javascript_mime_and_page_csp(snapshot, snapshot_http, monkeypatch):
    dashboard, get = legacy(snapshot, snapshot_http, monkeypatch)
    expected = (ROOT / "domains/supply_chain_operations/dashboard/load-errors.js").read_bytes()
    assert (dashboard / "load-errors.js").read_bytes() == expected
    status, headers, body = get("/supply-chain/load-errors.js?v=owned-regression")
    assert status == 200 and body == expected
    assert headers["Content-Type"].split(";")[0] in {"application/javascript", "text/javascript"}
    assert headers["Content-Length"] == str(len(expected))
    assert headers["X-Content-Type-Options"] == "nosniff"
    page_status, page_headers, _ = get("/supply-chain/")
    assert page_status == 200 and page_headers["Cache-Control"] == "no-store"
    policy = page_headers["Content-Security-Policy"]
    assert "script-src 'self'" in policy and "style-src 'self'" in policy
    assert "unsafe-inline" not in policy and "sha256-" not in policy


def test_unregistered_existing_javascript_stays_denied(snapshot, snapshot_http, monkeypatch):
    dashboard, get = legacy(snapshot, snapshot_http, monkeypatch)
    (dashboard / "unregistered.js").write_bytes(SENTINEL)
    status, _, body = get("/supply-chain/unregistered.js")
    assert status == 404 and SENTINEL not in body


def test_supply_route_cannot_escape_to_owned_parent_sentinel(snapshot, snapshot_http, monkeypatch):
    dashboard, get = legacy(snapshot, snapshot_http, monkeypatch)
    (dashboard.parent / "sentinel.js").write_bytes(SENTINEL)
    for target in ("/supply-chain/../sentinel.js", "/supply-chain/%2e%2e/sentinel.js", "/supply-chain/..%5csentinel.js"):
        status, _, body = get(target)
        assert status == 404 and SENTINEL not in body
