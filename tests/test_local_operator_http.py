"""Actual private HTTP and Windows owner producer; no production provider."""
from contextlib import contextmanager
import http.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import sqlite3
from threading import Thread

import pytest

from test_private_local_final_review import _ready
from shared_platform.common_offer_authority_store import CommonAuthorityBlocked
from shared_platform.local_operator_session import (
    initialize_private_browser_handoff, issue_private_browser_handoff,
    consume_private_browser_handoff,
)
from shared_platform.local_operator_http import PrivateLocalReviewHttp, handle_private_local_review, PREFIX


@contextmanager
def _service(store=None):
    class Handler(BaseHTTPRequestHandler):
        def _json(self, status, value):
            raw = json.dumps(value).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        def do_GET(self):
            if not handle_private_local_review(self, method='GET'):
                self._json(404, {})
        def do_POST(self):
            if not handle_private_local_review(self, method='POST'):
                self._json(404, {})
        def log_message(self, *_):
            pass
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    if store:
        httpd.private_local_review = PrivateLocalReviewHttp(store)
    worker = Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()
        worker.join(5)


def _request(httpd, action, body=None, *, headers=None, method='POST'):
    conn = http.client.HTTPConnection('127.0.0.1', httpd.server_port, timeout=5)
    values = {'Origin': f'http://127.0.0.1:{httpd.server_port}'}
    if body is not None:
        values['Content-Type'] = 'application/json'
    values.update(headers or {})
    conn.request(method, PREFIX + action, body=json.dumps(body) if body is not None else None, headers=values)
    response = conn.getresponse()
    status, fields, raw = response.status, response.getheaders(), response.read()
    conn.close()
    return status, fields, json.loads(raw)


def _bootstrap(store, httpd):
    initialize_private_browser_handoff(store.sessions)
    nonce = issue_private_browser_handoff(store.sessions, port=httpd.server_port, reservation_id='private-reservation')
    status, fields, value = _request(httpd, 'bootstrap', {'handoff': nonce})
    assert status == 200 and value['session_ready'] is True
    cookies = [value for name, value in fields if name.lower() == 'set-cookie']
    op, csrf = httpd.private_local_review.names(httpd.server_port)
    assert len(cookies) == 2
    assert 'HttpOnly' in next(cookie for cookie in cookies if cookie.startswith(op + '='))
    csrf_cookie = next(cookie for cookie in cookies if cookie.startswith(csrf + '='))
    assert 'HttpOnly' not in csrf_cookie
    token = csrf_cookie.split(';')[0].split('=', 1)[1]
    return nonce, {'Cookie': '; '.join(cookie.split(';')[0] for cookie in cookies), 'X-Orbit-CSRF-Token': token}


def test_production_default_does_not_install_private_channel():
    with _service() as httpd:
        status, _, value = _request(httpd, 'bootstrap', {'handoff': '0' * 64})
    assert status == 503 and value['error'] == 'LOCAL_OPERATOR_FEATURE_DISABLED'


def test_real_windows_owner_handoff_one_time_and_origin_bound(tmp_path):
    store, _, _ = _ready(tmp_path)
    initialize_private_browser_handoff(store.sessions)
    nonce = issue_private_browser_handoff(store.sessions, port=43210, reservation_id='private-reservation')
    with pytest.raises(CommonAuthorityBlocked, match='EXPIRED_OR_USED'):
        consume_private_browser_handoff(store.sessions, nonce, port=43211)
    grant, review_path = consume_private_browser_handoff(store.sessions, nonce, port=43210)
    with pytest.raises(CommonAuthorityBlocked, match='EXPIRED_OR_USED'):
        consume_private_browser_handoff(store.sessions, nonce, port=43210)
    with sqlite3.connect(store.authority.store.path) as db:
        row = db.execute('SELECT grant_json,consumed_at_epoch FROM operator_browser_handoffs').fetchone()
    assert row[0] is None and row[1] is not None
    assert grant.capability != nonce and grant.csrf != nonce
    assert review_path.startswith('/product-workspace?offer_id=') and '&plan_id=' in review_path


def test_actual_http_same_owner_one_decision_and_lost_response_replay(tmp_path):
    store, _, _ = _ready(tmp_path)
    with _service(store) as httpd:
        nonce, headers = _bootstrap(store, httpd)
        assert _request(httpd, 'bootstrap', {'handoff': nonce})[0] == 403
        status, _, prepared = _request(httpd, 'prepare', {'reservation_id': 'private-reservation'}, headers=headers)
        assert status == 200
        body = {'nonce': prepared['nonce'], 'review_digest': prepared['review_digest']}
        first = _request(httpd, 'decision', body, headers=headers)
        replay = _request(httpd, 'decision', body, headers=headers)
        assert first[0] == replay[0] == 200 and first[2] == replay[2]
        assert first[2]['execution_authority'] is False
    with sqlite3.connect(store.authority.store.path) as db:
        assert db.execute('SELECT COUNT(*) FROM private_final_decisions').fetchone()[0] == 1


@pytest.mark.parametrize('kind', ['foreign-origin', 'foreign-host', 'wrong-csrf', 'caller-approved-by', 'no-cookie'])
def test_actual_http_caller_substitutions_have_zero_decisions(tmp_path, kind):
    store, _, _ = _ready(tmp_path)
    with _service(store) as httpd:
        _, headers = _bootstrap(store, httpd)
        body = {'reservation_id': 'private-reservation'}
        if kind == 'foreign-origin':
            headers['Origin'] = 'http://127.0.0.1:12345'
        elif kind == 'foreign-host':
            headers['Host'] = 'localhost:' + str(httpd.server_port)
        elif kind == 'wrong-csrf':
            headers['X-Orbit-CSRF-Token'] = '0' * 64
        elif kind == 'caller-approved-by':
            body['approved_by'] = 'Kyle'
        else:
            headers.pop('Cookie')
        assert _request(httpd, 'prepare', body, headers=headers)[0] == 403
    with sqlite3.connect(store.authority.store.path) as db:
        assert db.execute('SELECT COUNT(*) FROM private_final_decisions').fetchone()[0] == 0


def test_cookie_names_separate_instances_and_ports(tmp_path):
    (tmp_path / 'left').mkdir()
    (tmp_path / 'right').mkdir()
    left, _, _ = _ready(tmp_path / 'left')
    right, _, _ = _ready(tmp_path / 'right')
    assert PrivateLocalReviewHttp(left).names(43210) != PrivateLocalReviewHttp(left).names(43211)
    assert PrivateLocalReviewHttp(left).names(43210) != PrivateLocalReviewHttp(right).names(43210)
