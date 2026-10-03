from __future__ import annotations

import json
from http.server import ThreadingHTTPServer
from threading import Thread
from urllib.request import Request, urlopen
from urllib.error import HTTPError

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


def test_workbench_task_changes_keep_local_audit_without_notifications(local_api):
    status, created = _request(local_api + "/api/workbench/tasks", {"title": "Prepare weekly review", "project": "ops", "priority": "P1"})
    assert status == 201
    task_id = created["task"]["task_id"]
    status, moved = _request(local_api + f"/api/workbench/tasks/{task_id}/transition", {"status": "in_progress"})
    assert status == 200 and moved["task"]["status"] == "in_progress"
    status, moved = _request(local_api + f"/api/workbench/tasks/{task_id}/transition", {"status": "waiting_approval"})
    assert status == 200 and moved["task"]["status"] == "waiting_approval"
    status, assigned = _request(local_api + f"/api/workbench/tasks/{task_id}", {"owner": "ops"})
    assert status == 200 and assigned["task"]["owner"] == "ops"
    _, detail = _request(local_api + f"/api/workbench/tasks/{task_id}")
    assert not any(event['event_type'].startswith('notification_') for event in detail['events'])
    status, dashboard = _request(local_api + "/api/workbench/dashboard")
    assert status == 200 and dashboard["counts"]["inbox"] == 0


@pytest.mark.parametrize('path', ['/api/feishu/event', '/api/digest/send', '/api/workbench/inbox/import'])
def test_retired_notification_routes_do_not_create_tasks(local_api, path):
    with pytest.raises(HTTPError) as exc:
        _request(local_api + path, {'message_id': 'om_123', 'text': 'retired input'})
    assert exc.value.code == 404
    _, dashboard = _request(local_api + '/api/workbench/dashboard')
    assert sum(dashboard['counts'].values()) == 0


def test_digest_preview_and_cli_are_retired(local_api):
    from main import build_parser
    with pytest.raises(HTTPError) as exc:
        _request(local_api + '/api/digest/preview')
    assert exc.value.code == 404
    parser = build_parser()
    for command in ('digest', 'feishu'):
        with pytest.raises(SystemExit) as error:
            parser.parse_args([command])
        assert error.value.code == 2


@pytest.mark.parametrize('path', ['/api/mx/approvals', '/api/uk/approvals', '/api/promotions', '/api/deactivate', '/api/mx/approvals/test/publish', '/api/uk/approvals/test/publish', '/api/promotions/push'])
def test_retired_operations_api_rejects_reads_and_writes(local_api, path):
    for payload in (None, {'items': [], 'token': 'test'}):
        with pytest.raises(HTTPError) as exc:
            _request(local_api + path, payload)
        assert exc.value.code == 404


def test_retired_operations_cli_rejects_legacy_entrypoints():
    from main import build_parser
    parser = build_parser()
    retired = [['affiliate'], ['products', 'title-serve']]
    retired += [['products', command] for command in ('deactivate-scan', 'deactivate-push', 'promo-scan', 'promo-push', 'coupon-scan')]
    retired += [['serve', '--page', page] for page in ('mx', 'uk', 'promotions', 'deactivate', 'titles', 'images')]
    for args in retired:
        with pytest.raises(SystemExit) as exc:
            parser.parse_args(args)
        assert exc.value.code == 2
    assert parser.parse_args(['products', 'image-scan']).products_cmd == 'image-scan'


def test_retired_report_inbox_is_unavailable(local_api):
    with pytest.raises(HTTPError) as exc:
        _request(local_api + '/api/orbit/inbox')
    assert exc.value.code == 404
