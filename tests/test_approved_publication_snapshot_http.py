from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
import sqlite3
from threading import Thread
import urllib.error
import urllib.parse
import urllib.request

import pytest

from modules.products import server as product_server
from test_approved_publication_snapshot_integration import _approved_full_store


def _get(base_url: str, query: dict[str, object]) -> tuple[int, dict]:
    url = (
        base_url
        + "/api/product-workspace/publication-snapshot?"
        + urllib.parse.urlencode(query)
    )
    try:
        with urllib.request.urlopen(url, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


@pytest.fixture
def snapshot_http_server(tmp_path, monkeypatch):
    store, payload, response = _approved_full_store(tmp_path, monkeypatch)
    monkeypatch.setattr(product_server, "_release_store", lambda: store)
    server = ThreadingHTTPServer(("127.0.0.1", 0), product_server.Handler)
    server.daemon_threads = True
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield (
            f"http://127.0.0.1:{server.server_address[1]}",
            store,
            payload,
            response["approval"]["publication_snapshot"]["snapshot_digest"],
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_public_snapshot_api_is_read_only_redacted_and_supports_both_identities(
    snapshot_http_server,
):
    base_url, store, payload, digest = snapshot_http_server
    before = store.path.read_bytes()
    by_plan_status, by_plan = _get(
        base_url,
        {"offer_id": payload["product_id"], "plan_id": payload["plan_id"]},
    )
    by_digest_status, by_digest = _get(
        base_url,
        {"offer_id": payload["product_id"], "snapshot_digest": digest},
    )

    assert by_plan_status == by_digest_status == 200
    assert by_plan == by_digest
    assert by_plan["status"] == "AVAILABLE"
    assert by_plan["coverage"] == {
        "publication_target_count": 2,
        "provider_category_count": 1,
        "sku_count": 1,
        "approved_image_count": 1,
    }
    encoded = json.dumps(by_plan, ensure_ascii=False)
    for forbidden in (
        "Approved removable PVC",
        "https://",
        "8.1",
        "34 x 58 cm",
        "600009",
        "source_offer_id",
        "source_item_code",
        "provenance",
    ):
        assert forbidden not in encoded
    assert store.path.read_bytes() == before

    internal = product_server._approved_publication_snapshot_internal(
        offer_id=payload["product_id"], plan_id=payload["plan_id"]
    )
    assert internal["snapshot_digest"] == digest
    assert internal["product"]["description"].startswith("Approved removable")


def test_snapshot_api_rejects_cross_offer_and_ambiguous_queries(
    snapshot_http_server,
):
    base_url, _store, payload, digest = snapshot_http_server
    cross_status, _cross = _get(
        base_url,
        {"offer_id": "9999999999", "plan_id": payload["plan_id"]},
    )
    ambiguous_status, _ambiguous = _get(
        base_url,
        {
            "offer_id": payload["product_id"],
            "plan_id": payload["plan_id"],
            "snapshot_digest": digest,
        },
    )
    missing_status, _missing = _get(
        base_url,
        {"offer_id": payload["product_id"]},
    )
    assert cross_status == 404
    assert ambiguous_status == 400
    assert missing_status == 400


def test_declared_snapshot_missing_row_is_integrity_failure(
    snapshot_http_server,
):
    base_url, store, payload, _digest = snapshot_http_server
    with sqlite3.connect(store.path) as connection:
        connection.execute(
            "DROP TRIGGER trg_approved_publication_snapshot_append_only"
        )
        connection.execute(
            "DELETE FROM approved_publication_snapshots WHERE plan_id = ?",
            (payload["plan_id"],),
        )
        connection.commit()

    status, response = _get(
        base_url,
        {"offer_id": payload["product_id"], "plan_id": payload["plan_id"]},
    )
    assert status == 409
    assert response == {
        "ok": False,
        "error": "approved publication snapshot integrity check failed",
    }
