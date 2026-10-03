"""Real HTTP parser/Handler and original engines, isolated from server startup."""
import ast
import hashlib
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import socketserver
from threading import Thread
from urllib.parse import urlparse

import pytest

from test_i05_profit_facts import captured_profile


@pytest.fixture
def http_fixture(record_property):
    path = Path(__file__).resolve().parents[1] / 'modules/products/server.py'
    raw = path.read_bytes()
    tree = ast.parse(raw)
    handler_node = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'Handler')
    limit_node = next(node for node in tree.body if isinstance(node, ast.Assign) and any(isinstance(target, ast.Name) and target.id == 'PRODUCT_APPROVAL_BODY_LIMIT' for target in node.targets))
    # Compile the complete original Handler unchanged, not a copied route or mock.
    # Exclude module startup/imports that open the business DB and start services.
    selected = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), limit_node, handler_node], type_ignores=[])
    namespace = {'BaseHTTPRequestHandler': BaseHTTPRequestHandler, 'json': json, 'urlparse': urlparse}
    exec(compile(ast.fix_missing_locations(selected), str(path), 'exec'), namespace)

    class LocalHTTPServer(HTTPServer):
        def server_bind(self):
            socketserver.TCPServer.server_bind(self)
            self.server_name = 'localhost'
            self.server_port = self.server_address[1]

    server = LocalHTTPServer(('127.0.0.1', 0), namespace['Handler'])
    thread = Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
    thread.start()
    receipts = []

    def request(method='GET', payload=None, headers=None, raw_body=None):
        body = raw_body if raw_body is not None else json.dumps(payload).encode() if payload is not None else b''
        actual_headers = {'Content-Type': 'application/json', **(headers or {})}
        connection = HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        try:
            connection.request(method, '/api/profit-center/captured-review', body=body, headers=actual_headers)
            response = connection.getresponse()
            result = json.loads(response.read())
            receipts.append({'method': method, 'status': response.status, 'payload': result, 'url': f'http://127.0.0.1:{server.server_port}/api/profit-center/captured-review'})
            return response.status, result
        finally:
            connection.close()

    yield request
    server.shutdown()
    thread.join(timeout=3)
    server.server_close()
    record_property('handler_source_sha256', hashlib.sha256(raw).hexdigest())
    record_property('http_receipts', json.dumps(receipts, ensure_ascii=False))


def test_http_get_reads_no_selected_inputs(http_fixture):
    status, payload = http_fixture()
    assert status == 200 and payload['status'] == 'input_required'
    assert payload['network_reads_performed'] == []


def test_fixture_http_permission_does_not_allow_external_connections():
    import os
    import socket
    if not os.environ.get('I05_ALLOW_LOOPBACK_HTTP'):
        pytest.skip('audit startup guard probe')
    with socket.socket() as connection:
        with pytest.raises(RuntimeError, match='I05_OFFLINE_GUARD socket.connect'):
            connection.connect(('198.51.100.1', 443))


@pytest.mark.parametrize('case,expected', [('profile', 400), ('bad_json', 400), ('origin', 403), ('host', 403), ('type', 415), ('size', 413)])
def test_http_rejects_invalid_requests_before_consumer(http_fixture, case, expected):
    headers = {}
    body = None
    if case == 'bad_json': body = b'{'
    elif case == 'origin': headers['Origin'] = 'https://example.invalid'
    elif case == 'host': headers['Host'] = 'example.invalid'
    elif case == 'type': headers['Content-Type'] = 'text/plain'
    elif case == 'size': body = b' ' * (64 * 1024 + 1)
    status, _ = http_fixture('POST', {}, headers=headers, raw_body=body)
    assert status == expected


@pytest.mark.parametrize('case,expected', [('host', 403), ('type', 415), ('size', 413)])
def test_rejected_unread_body_keeps_http_response(http_fixture, case, expected):
    headers = {'Host': 'example.invalid'} if case == 'host' else {'Content-Type': 'text/plain'} if case == 'type' else {}
    body = b' ' * (64 * 1024 + 1) if case == 'size' else b'{}'
    for _ in range(5):
        status, payload = http_fixture('POST', headers=headers, raw_body=body)
        assert status == expected and payload['status'] == 'check_failed'


def test_rejection_does_not_wait_for_declared_oversized_body(http_fixture):
    status, _ = http_fixture('POST', headers={'Content-Length': '1000000000'}, raw_body=b'')
    assert status == 413


@pytest.mark.parametrize('case', ['review', 'missing', 'empty', 'complete'])
def test_http_actual_captured_consumer(http_fixture, tmp_path, case):
    import sqlite3
    profile = captured_profile(tmp_path)
    if case == 'missing':
        profile['catalog_path'] = str(tmp_path / 'missing.db')
    elif case == 'empty':
        path = Path(profile['evidence_path'])
        evidence = json.loads(path.read_text())
        evidence.update(orders=[], net_settlement_total_local='0')
        path.write_text(json.dumps(evidence))
    elif case == 'complete':
        with sqlite3.connect(profile['catalog_path']) as db:
            db.execute("UPDATE sku_costs SET cost_cny='8'")
        path = Path(profile['evidence_path'])
        evidence = json.loads(path.read_text())
        evidence['orders'][0]['financial_components'] = [{'code': 'import_vat', 'amount': '1'}, {'code': 'customs_duty', 'amount': '1'}]
        path.write_text(json.dumps(evidence))
    status, payload = http_fixture('POST', {'profile': profile})
    assert status == (400 if case == 'missing' else 200)
    assert payload['network_reads_performed'] == []
    if case == 'missing':
        assert payload['status'] == 'check_failed'
    else:
        report = payload['reports']['tiktok']['report']
        assert report['source']['catalog_snapshot_id']
        assert report['source']['fx_snapshot']['checksum']
        if case == 'empty': assert report['totals']['profit_cny'] is None
        elif case == 'complete': assert report['status'] == 'ready'
        else: assert report['status'] == 'needs_review'
