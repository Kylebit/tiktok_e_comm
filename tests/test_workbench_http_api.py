from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.request import Request, urlopen

import pytest

from modules.products.server import Handler
from shared_platform import workbench_store


@pytest.fixture
def local_api(tmp_path, monkeypatch):
    store = workbench_store.WorkbenchStore(tmp_path / "workbench.db")
    monkeypatch.setattr(workbench_store, "default_workbench_store", lambda: store)
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _request(url: str, payload: dict | None = None):
    request = Request(url, data=json.dumps(payload).encode() if payload is not None else None, method="POST" if payload is not None else "GET", headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=5) as response:
        return response.status, json.loads(response.read())


def test_workbench_task_and_feishu_import_api(local_api):
    status, created = _request(local_api + "/api/workbench/tasks", {"title": "Prepare weekly review", "project": "ops", "priority": "P1"})
    assert status == 201
    task_id = created["task"]["task_id"]
    status, moved = _request(local_api + f"/api/workbench/tasks/{task_id}/transition", {"status": "in_progress"})
    assert status == 200 and moved["task"]["status"] == "in_progress"
    status, imported = _request(local_api + "/api/workbench/inbox/import", {"message_id": "om_123", "text": "Check approval card"})
    assert status == 201 and imported["task"]["status"] == "inbox"
    status, dashboard = _request(local_api + "/api/workbench/dashboard")
    assert status == 200 and dashboard["counts"]["inbox"] == 1
