"""Owned TCP transport over the complete Handler and synthetic durable evidence."""
import http.client
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timezone, timedelta
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

import pytest

from modules.products import server
from test_r3_closure_service import synthetic_closure_context
from test_r3_status_projection import approved_context, seed_historical_report

PREFIX = '/api/product-workspace/publication-closure/'


@contextmanager
def owned_http(tmp_path):
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    records = []

    def call(action, body=None, *, headers=None, raw=None):
        connection = http.client.HTTPConnection('127.0.0.1', httpd.server_port, timeout=5)
        method = 'GET' if body is None and raw is None else 'POST'
        content = raw if raw is not None else None if body is None else json.dumps(body)
        try:
            connection.request(method, PREFIX + action, body=content,
                               headers={'Content-Type': 'application/json', **(headers or {})})
            response = connection.getresponse()
            result = json.loads(response.read())
            records.append({'method': method, 'action': action, 'status': response.status, 'body': result})
            return response.status, result
        finally:
            connection.close()

    try:
        yield call
    finally:
        httpd.shutdown(); worker.join(timeout=3); httpd.server_close()
        (tmp_path / 'owned-closure-tcp.json').write_text(json.dumps({'port': httpd.server_port, 'requests': records}, indent=2))


def latest_action(data):
    return 'latest?' + urlencode({key: data[key] for key in ('offer_id', 'plan_id')})


def test_owned_closure_roundtrip_concurrent_and_rejection_retry(tmp_path, monkeypatch):
    _, data, transport, market, _, service, inputs = synthetic_closure_context(tmp_path, monkeypatch, 'PROCESSING')
    root = service.report_store.reports_root
    with owned_http(tmp_path) as call:
        before = {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
        assert call(latest_action(data))[1]['status'] == 'NOT_RECORDED'
        status, prepared = call('prepare', inputs)
        assert status == 200 and prepared['status'] == 'READY_TO_RECORD'
        assert before == {str(p): p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
        assert [r['target_label'] for r in prepared['target_results']] == market['targets']
        body = {**inputs, 'input_digest': prepared['input_digest']}
        assert call('record', body, headers={'Host': 'localhost.evil:8765'})[0] == 403
        assert call('record', body, headers={'Origin': 'https://evil.example'})[0] == 403
        assert call('record', raw='{"offer_id":"1","offer_id":"2"}')[0] == 400
        assert call('record', {**body, 'root': 'client-override'})[0] == 400
        assert call('record', {**body, 'input_digest': 'sha256:' + '0' * 64})[0] == 409
        assert not list(root.rglob('closure-report.json'))
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: call('record', body), range(2)))
        assert all(status == 200 for status, _ in results), results
        assert results[0] == results[1]
        recorded = results[0][1]
        assert recorded['local_persistence'] == 'IMMUTABLE_RECORD_CONFIRMED'
        assert recorded['closure']['closure_status'] == 'CLOSED_WITH_OPEN_ITEMS'
        assert recorded['closure']['summary']['verified_count'] == 1
        files = list(root.rglob('closure-report.json'))
        assert len(files) == 1
        disk = files[0].read_bytes()
        assert json.loads(disk) == recorded['closure']
        assert call('record', body) == results[0]
        assert call(latest_action(data))[1]['closure'] == recorded['closure']
        assert files[0].read_bytes() == disk
        assert len(service.report_store.list_reports(offer_id=data['offer_id'])) == 2
        assert transport.mutations == 1


@pytest.mark.parametrize('damage', ['missing_target', 'unknown', 'missing_report', 'input_drift'])
def test_owned_closure_blocks_incomplete_or_stale_evidence(tmp_path, monkeypatch, damage):
    if damage == 'missing_target':
        store, data, transport, market, snapshot = approved_context(tmp_path, monkeypatch)
        seed_historical_report(store, data, market, snapshot, run_id='first', state='PUBLISHED')
        inputs = {key: data[key] for key in ('offer_id', 'plan_id')}
        inputs.update(recorded_by='Synthetic reviewer', recorded_at=(datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat())
    else:
        store, data, transport, market, snapshot, _, inputs = synthetic_closure_context(tmp_path, monkeypatch)
    reports = server._product_publication_report_store()
    with owned_http(tmp_path) as call:
        status, prepared = call('prepare', inputs)
        if damage == 'missing_target':
            assert status == 409 and prepared['closure'] is None
            assert [r['target_label'] for r in prepared['target_results']] == market['targets']
            assert any(r['status'] == 'NOT_RUN' for r in prepared['target_results'])
        else:
            assert status == 200
            body = {**inputs, 'input_digest': prepared['input_digest']}
            if damage == 'unknown':
                seed_historical_report(store, data, market, snapshot, run_id='unknown-later', state='FAILED', unknown=True)
            elif damage == 'missing_report':
                source = reports.get_report_by_run(run_id='first')
                (reports.reports_root / source['report_path']).unlink()
            else:
                body['recorded_by'] = 'Changed synthetic reviewer'
            status, result = call('record', body)
            assert status == 409 and not result['ok']
        assert not list(reports.reports_root.rglob('closure-report.json'))
        assert transport.mutations == 1


def test_owned_latest_rejects_new_unknown_without_replacing_history(tmp_path, monkeypatch):
    store, data, transport, market, snapshot, service, inputs = synthetic_closure_context(tmp_path, monkeypatch)
    with owned_http(tmp_path) as call:
        _, prepared = call('prepare', inputs)
        assert call('record', {**inputs, 'input_digest': prepared['input_digest']})[0] == 200
        path = next(service.report_store.reports_root.rglob('closure-report.json'))
        historical = path.read_bytes()
        seed_historical_report(store, data, market, snapshot, run_id='unknown-later', state='FAILED', unknown=True)
        status, result = call(latest_action(data))
        assert status == 409 and result['code'] == 'CLOSURE_EVIDENCE_CONFLICT'
        assert 'closure' not in result and path.read_bytes() == historical
        assert transport.mutations == 1
