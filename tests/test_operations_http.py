import io
from email.message import Message
from types import SimpleNamespace
from shared_platform.operations_http import handle


def request(headers, body=b'{}'):
    messages = []
    parsed = Message()
    for key,value in headers:
        parsed[key] = value
    return SimpleNamespace(path='/api/orbit/tasks', headers=parsed,
        client_address=('127.0.0.1',1234),server=SimpleNamespace(server_port=49321),
        rfile=io.BytesIO(body),_json=lambda *value: messages.append(value)), messages


def test_foreign_origin_rejected_before_task_dispatch():
    h,messages=request([('Host','127.0.0.1:49321'),('Origin','https://example.com')])
    assert handle(h,method='POST',runtime=None)
    assert messages[0][0] == 403


def test_malformed_and_ambiguous_host_fail_closed():
    for headers in [[('Host','[')], [('Host','127.0.0.1:49321'),('Host','evil.example')]]:
        h,messages=request(headers)
        assert handle(h,method='POST',runtime=None)
        assert messages[0][0] == 403


def test_ambiguous_body_length_never_dispatches():
    h,messages=request([('Host','127.0.0.1:49321'),('Content-Type','application/json'),('Content-Length','2'),('Content-Length','2')])
    assert handle(h,method='POST',runtime=None)
    assert messages[0][0] == 400


def test_host_cannot_contain_url_components():
    for host in ['localhost:49321#x','localhost:49321/path','user@localhost:49321','localhost:49321?x']:
        h,messages=request([('Host',host)])
        assert handle(h,method='POST',runtime=None)
        assert messages[0][0] == 403
