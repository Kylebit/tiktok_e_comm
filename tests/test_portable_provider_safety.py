from __future__ import annotations

import importlib.util
import io
import os
from pathlib import Path
from urllib.error import HTTPError

import pytest

ROOT = Path(__file__).resolve().parents[1]


def source(name):
    preserved = os.environ.get('ORBIT_TEST_PRESERVED_PROVIDER_ROOT')
    if preserved:
        paths = {'duoplus': 'installed/control-duoplus-cloud-phone/scripts/duoplus_client.py',
                 'tikhub': 'r4-c-scripts/tikhub_reference_probe.py'}
        path = Path(preserved) / paths[name]
    else:
        path = ROOT / 'modules/tools' / (name + '.py')
    spec = importlib.util.spec_from_file_location('portable_test_' + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_duoplus_install_defaults_to_zero_write_preview(monkeypatch):
    m = source('duoplus'); client = m.DuoPlusClient(api_key='fixture-only')
    calls = []
    monkeypatch.setattr(client, 'post', lambda *args: calls.append(args) or {'code': 200})
    preview = client.install_app(['device-a'], 'app-a', 'version-a')
    assert calls == []
    assert preview['network_call_performed'] is False
    assert preview['payload']['app_version_id'] == 'version-a'


@pytest.mark.parametrize('origin', ['https://user@openapi.duoplus.cn', 'https://openapi.duoplus.cn:444'])
def test_duoplus_origin_does_not_allow_credentials_or_unapproved_port(origin):
    m = source('duoplus')
    with pytest.raises(m.DuoPlusError): m._validated_base_url(origin)


def test_duoplus_http_error_does_not_echo_provider_body(monkeypatch):
    m = source('duoplus'); client = m.DuoPlusClient(api_key='fixture-key')
    def fail(*args, **kwargs):
        raise HTTPError('https://openapi.duoplus.cn', 401, 'Denied', {}, io.BytesIO(b'fixture-key private-response'))
    monkeypatch.setattr(m.request, 'urlopen', fail)
    if hasattr(m, 'open_request'): monkeypatch.setattr(m, 'open_request', fail)
    with pytest.raises(m.DuoPlusError) as caught: client.devices()
    assert 'fixture-key' not in str(caught.value)
    assert 'private-response' not in str(caught.value)


def test_tikhub_billable_http_400_is_attempted_once(monkeypatch):
    m = source('tikhub'); calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise HTTPError('https://api.tikhub.io', 400, 'Bad Request', {}, io.BytesIO(b'fixture response'))
    monkeypatch.setattr(m.urllib.request, 'urlopen', fail)
    if hasattr(m, 'open_request'): monkeypatch.setattr(m, 'open_request', fail)
    with pytest.raises(RuntimeError): m.request_json(m.PRODUCT_SEARCH_PATH, {'search_word': 'fixture'}, 'fixture-key')
    assert len(calls) == 1
