"""Exact signed-request echo counterexamples; closed transport, fake app scope."""
import base64
import gzip
import hashlib
import json
from types import SimpleNamespace

import pytest

from modules.miaoshou import client
from test_common_detail_observation import CONFIG, LocalResponse


def install_actual_signed_response(monkeypatch, response_body):
    calls = []

    def closed(request, timeout):
        assert request.full_url == client.OPEN_BASE_URL + client.COMMON_DETAIL_OBSERVATION_PATH
        assert request.get_method() == 'POST' and timeout == 30
        signature = request.get_header('X-sign')
        assert signature and len(signature) == 64
        assert signature == client._open_sign(CONFIG['app_secret'],
            client.COMMON_DETAIL_OBSERVATION_PATH, int(request.get_header('X-timestamp')),
            CONFIG['app_id'], request.data.decode('utf-8'))
        wire, encoding, business = response_body(signature)
        calls.append({'wire': wire, 'business': business})
        return LocalResponse(wire, encoding)

    monkeypatch.setattr(client, '_wait_for_open_slot', lambda: None)
    monkeypatch.setattr(client.urllib.request, 'build_opener',
        lambda *handlers: SimpleNamespace(open=closed))
    # Refuse a regression that accidentally falls back to the legacy transport.
    monkeypatch.setattr(client.urllib.request, 'urlopen',
        lambda *args, **kwargs: pytest.fail('unexpected legacy or network transport'))
    return calls


@pytest.mark.parametrize('echo', ['plain_value', 'plain_key', 'escaped_value', 'escaped_key', 'gzip_value'])
def test_actual_request_signature_echo_is_refused_before_receipt(monkeypatch, echo):
    def body(signature):
        encoded = ''.join('\\u' + format(ord(c), '04x') for c in signature)
        if echo == 'plain_key':
            business = json.dumps({'result': 'success', 'data': {signature: 'ordinary-value'}}).encode()
        elif echo == 'escaped_key':
            business = ('{"result":"success","data":{"' + encoded + '":"ordinary-value"}}').encode()
        elif echo == 'escaped_value':
            business = ('{"result":"success","data":{"ordinaryField":"' + encoded + '"}}').encode()
        else:
            business = json.dumps({'result': 'success', 'data': {'ordinaryField': signature}}).encode()
        if echo == 'gzip_value':
            return gzip.compress(business, mtime=0), 'gzip', business
        return business, 'identity', business

    calls = install_actual_signed_response(monkeypatch, body)
    with pytest.raises(ValueError, match='^COMMON_OBSERVER_SENSITIVE_RESPONSE_REFUSED$'):
        client.NativeCommonDetailObserver(CONFIG).observe('123')
    assert len(calls) == 1


@pytest.mark.parametrize('compressed', [False, True])
def test_normal_signed_observation_retains_exact_business_and_wire(monkeypatch, compressed):
    business = b'{\n "result": "success", "data": {"ordinaryField": "business-fact"}\n}'
    wire = gzip.compress(business, mtime=0) if compressed else business
    calls = install_actual_signed_response(monkeypatch,
        lambda signature: (wire, 'gzip' if compressed else 'identity', business))
    observation = client.NativeCommonDetailObserver(CONFIG).observe('123')
    receipt = observation.receipt()
    assert len(calls) == 1
    assert observation.business_bytes == business and observation.wire_bytes == wire
    assert base64.b64decode(receipt['business_base64']) == business
    assert base64.b64decode(receipt['wire_base64']) == wire
    assert receipt['business_sha256'] == hashlib.sha256(business).hexdigest()
    assert receipt['wire_sha256'] == hashlib.sha256(wire).hexdigest()
    assert receipt['execution_authority'] is False
