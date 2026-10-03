"""Real local HTTP contract for the read-only Skill identity projection."""

import http.client
from email.message import Message
import json
from types import SimpleNamespace

from test_unified_entry import entry_http  # noqa: F401 - isolated socket fixture


def _request(port: int, *hosts: str):
    connection = http.client.HTTPConnection('127.0.0.1', port, timeout=10)
    try:
        connection.putrequest('GET', '/api/orbit/skill-identity', skip_host=True)
        for host in hosts:
            connection.putheader('Host', host)
        connection.endheaders()
        response = connection.getresponse()
        return response.status, dict(response.headers), response.read()
    finally:
        connection.close()


def test_real_route_rejects_rebinding_and_only_returns_minimal_snapshot(entry_http):
    _, server, _ = entry_http
    port = server.server_port
    for hosts in [(), ('localhost.evil:%s' % port,), ('evil.example:%s' % port,),
                  ('127.0.0.1',), ('127.0.0.1:%s' % (port + 1),),
                  ('127.0.0.1:%s' % port, 'evil.example:%s' % port)]:
        status, headers, body = _request(port, *hosts)
        assert status == 403, (hosts, status)
        assert headers['Cache-Control'] == 'no-store'
        assert json.loads(body)['code'] == 'SKILL_IDENTITY_LOOPBACK_REQUIRED'
    status, headers, body = _request(port, f'127.0.0.1:{port}')
    assert status == 200
    assert headers['Cache-Control'] == 'no-store'
    assert headers['X-Content-Type-Options'] == 'nosniff'
    payload = json.loads(body)
    assert payload['schema'] == 'orbit-skill-identity/v1'
    assert payload['execution_authority'] is False
    assert len(payload['skills']) == 7
    assert all('comparison' in row for row in payload['skills'].values())
    for forbidden in (b'C:\\Users', b'D:\\', b'E:\\', b'resolved_target', b'"files"',
                      b'ProgramData', b'"path"', b'"raw_digest"', b'SKILL.md'):
        assert forbidden not in body


def test_local_host_alone_cannot_authorize_nonloopback_peer():
    from modules.products.server import Handler

    headers = Message()
    headers['Host'] = '127.0.0.1:9999'
    observed = []
    request = SimpleNamespace(
        headers=headers, client_address=('203.0.113.8', 12345),
        server=SimpleNamespace(server_address=('127.0.0.1', 9999)),
        _json=lambda status, payload: observed.append((status, payload)),
    )
    assert Handler._skill_identity_local_host(request) is False
    assert observed == [(403, {'ok': False, 'code': 'SKILL_IDENTITY_LOOPBACK_REQUIRED'})]
