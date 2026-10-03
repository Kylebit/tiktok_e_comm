"""Named production consumers through the real opener, lowest HTTP response fake.

No resource helper or consumer download method is replaced. All URLs and bytes
are synthetic; even accidentally unhandled transports fail under the audit guard.
"""
from email.message import Message
import hashlib
import http.client
import io
from pathlib import Path
import socket
import ssl
from types import SimpleNamespace
from unittest.mock import patch
import urllib.request
from urllib.response import addinfourl

from PIL import Image
import pytest

from core import http_retry
from modules.sourcing import pipeline, scrape_1688
from modules.catalog import pdf_export
# global_sku_map reads routing defaults at import. Supply an empty synthetic
# configuration before importing it; never create/read a real settings file.
with patch('core.config.load_settings', return_value={}):
    from modules.shopee import publish


def png():
    buf = io.BytesIO()
    Image.new('RGB', (8, 6), (25, 100, 200)).save(buf, format='PNG')
    return buf.getvalue()


PNG = png()
HTML = b'<html><body><img src="https://img.alicdn.com/synthetic.png"></body></html>'
START = 'https://source.invalid/start'
FINAL = 'https://cdn.invalid/final'


class Body(io.BytesIO):
    def __init__(self, raw):
        super().__init__(raw)
        self.requests = []
        self.delivered = 0

    def read(self, size=-1):
        self.requests.append(size)
        value = super().read(size)
        self.delivered += len(value)
        return value


@pytest.fixture
def wire(monkeypatch, record_property):
    queue, calls, bodies = [], [], []
    monkeypatch.setattr(urllib.request, 'getproxies', lambda: {})
    monkeypatch.setattr(socket, 'getaddrinfo', lambda host, port, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('93.184.216.34', port))])
    monkeypatch.setattr(http_retry.time, 'sleep', lambda _: None)

    def fake_https(handler, req):
        context = getattr(handler, '_context', None)
        calls.append({'url': req.full_url, 'method': req.get_method(), 'headers': dict(req.header_items()), 'host': req.host, 'tunnel': getattr(req, '_tunnel_host', None), 'verify_mode': int(context.verify_mode) if context else None, 'check_hostname': context.check_hostname if context else None})
        assert queue, 'unexpected additional HTTP request'
        row = queue.pop(0)
        if isinstance(row, BaseException):
            raise row
        status, raw, headers = row
        msg = Message()
        for name, value in headers.items():
            msg[name] = value
        body = raw if isinstance(raw, Body) else Body(raw)
        bodies.append(body)
        response = addinfourl(body, msg, req.full_url, status)
        response.msg = 'Synthetic'
        return response

    monkeypatch.setattr(urllib.request.HTTPSHandler, 'https_open', fake_https)
    monkeypatch.setattr(urllib.request.HTTPHandler, 'http_open', fake_https)
    # The baseline's unsafe curl path is modeled only where explicitly tested.
    def no_curl(*a, **k):
        raise AssertionError('resource consumer must not launch curl')
    monkeypatch.setattr(publish.subprocess, 'run', no_curl)
    yield SimpleNamespace(queue=queue, calls=calls, bodies=bodies)
    assert all(body.closed for body in bodies), 'success, redirect and failed responses must all close'
    record_property('lowest_http_calls', calls)
    record_property('delivered_bytes', [body.delivered for body in bodies])


def ok(raw=PNG, mime='image/png', **headers):
    return (200, raw, {'Content-Type': mime, **headers})


def redirect(target, status=302):
    return status, b'', {'Location': target}


def consume(name, tmp_path, url=START):
    if name == 'html':
        return scrape_1688._fetch(url)
    if name == 'pdf':
        return pdf_export._download_image(url)
    dest = tmp_path / (name + '.png')
    if name == 'pipeline':
        pipeline._download_url(url, dest)
    else:
        assert publish._download_image(url, dest) == dest
    return dest.read_bytes()


@pytest.mark.parametrize('name', ['pipeline', 'html', 'pdf', 'shopee'])
def test_real_consumer_follows_relative_then_cross_cdn_redirect(wire, tmp_path, name, record_property):
    wire.queue.extend([redirect('/relative'), redirect(FINAL), ok(HTML, 'text/html; charset=utf-8') if name == 'html' else ok()])
    result = consume(name, tmp_path)
    if name == 'pdf':
        assert result is not None, 'valid redirected image must reach PDF ImageReader'
        assert result.getSize() == (8, 6)
        assert len(result.getRGBData()) == 8 * 6 * 3
        record_property('decoded_rgb_sha256', hashlib.sha256(result.getRGBData()).hexdigest())
    elif name == 'html':
        assert result == HTML.decode()
    else:
        assert hashlib.sha256(result).digest() == hashlib.sha256(PNG).digest()
        record_property('output_sha256', hashlib.sha256(result).hexdigest())
    assert [call['url'] for call in wire.calls] == [START, 'https://source.invalid/relative', FINAL]
    assert all(call['check_hostname'] and call['verify_mode'] == 2 for call in wire.calls)


@pytest.mark.parametrize('bad', [b'<html>upstream error</html>', PNG[:40]])
def test_shopee_curl_exit_zero_must_not_accept_error_or_incomplete_image(wire, monkeypatch, tmp_path, bad):
    dest = tmp_path / 'existing.png'
    dest.write_bytes(PNG)
    def unsafe_curl(argv, **kwargs):
        Path(argv[argv.index('-o') + 1]).write_bytes(bad)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(publish.subprocess, 'run', unsafe_curl)
    wire.queue.append(ok(bad))
    with pytest.raises(Exception):
        publish._download_image(START, dest)
    assert dest.read_bytes() == PNG


def test_error_page_never_reaches_shopee_upload_consumer(wire, monkeypatch):
    attempted_uploads = []
    def upload(path, **kwargs):
        attempted_uploads.append(Path(path).read_bytes())
        return {'image_info': {'image_id': 'synthetic'}}
    monkeypatch.setattr(publish, 'upload_image', upload)
    wire.queue.append(ok(b'<html>unavailable</html>', 'text/html'))
    with pytest.raises(Exception):
        publish._upload_images_exact([START])
    assert attempted_uploads == []


@pytest.mark.parametrize('name', ['pipeline', 'html', 'pdf', 'shopee'])
def test_direct_200_real_consumers(wire, tmp_path, name):
    wire.queue.append(ok(HTML, 'text/html') if name == 'html' else ok())
    result = consume(name, tmp_path)
    assert result is not None
    assert len(wire.calls) == 1


@pytest.mark.parametrize('name', ['pipeline', 'html', 'pdf', 'shopee'])
@pytest.mark.parametrize('case', ['loop', 'budget', 'downgrade', 'userinfo', 'private-target', 'protocol'])
def test_redirect_boundary_at_real_consumers(wire, tmp_path, name, case):
    rows = {
        'loop': [redirect('/next'), redirect('/start')],
        'budget': [redirect('/a'), TimeoutError(), redirect('/b'), redirect('/c'), ok()],
        'downgrade': [redirect('http://cdn.invalid/final')],
        'userinfo': [redirect('https://synthetic:password@cdn.invalid/final')],
        'private-target': [redirect('https://127.0.0.1/final')],
        'protocol': [redirect('file:///synthetic.png')],
    }[case]
    wire.queue.extend(rows)
    dest = tmp_path / (name + '.png')
    if name in {'pipeline', 'shopee'}:
        dest.write_bytes(PNG)
    if name == 'pdf':
        assert consume(name, tmp_path) is None
    else:
        with pytest.raises(ValueError):
            consume(name, tmp_path)
    assert len(wire.calls) == {'loop': 2, 'budget': 4}.get(case, 1)
    if name in {'pipeline', 'shopee'}:
        assert dest.read_bytes() == PNG
        assert list(tmp_path.glob('*.part')) == []


@pytest.mark.parametrize('name', ['pipeline', 'html', 'pdf', 'shopee'])
def test_redirect_retry_shared_budget_can_succeed_in_four_calls(wire, tmp_path, name):
    wire.queue.extend([redirect('/retry'), TimeoutError(), redirect(FINAL), ok(HTML, 'text/html') if name == 'html' else ok()])
    assert consume(name, tmp_path) is not None
    assert [call['url'] for call in wire.calls] == [START, 'https://source.invalid/retry', 'https://source.invalid/retry', FINAL]


@pytest.mark.parametrize('name', ['pipeline', 'html', 'pdf', 'shopee'])
@pytest.mark.parametrize('bad_kind', ['huge', 'wrong-type', 'bad-content', 'interrupted'])
def test_streaming_limits_types_and_interruption_preserve_outputs(wire, tmp_path, name, bad_kind):
    cap = 5_000_000 if name in {'html', 'pdf'} else 10_000_000
    mime = 'text/html' if name == 'html' else 'image/png'
    if bad_kind == 'huge':
        wire.queue.append(ok(b'x' * (cap + 100_000), mime))
    elif bad_kind == 'wrong-type':
        wire.queue.append(ok(HTML, 'application/json'))
    elif bad_kind == 'bad-content':
        wire.queue.append(ok(b'not a valid resource', mime))
    else:
        class Interrupted(Body):
            def read(self, size=-1):
                if self.delivered:
                    raise ConnectionResetError('synthetic reset')
                return super().read(min(size, 20))
        wire.queue.extend([ok(Interrupted(HTML if name == 'html' else PNG), mime) for _ in range(4)])
    dest = tmp_path / (name + '.png')
    if name in {'pipeline', 'shopee'}:
        dest.write_bytes(PNG)
    if name == 'pdf':
        assert consume(name, tmp_path) is None
    else:
        with pytest.raises(ValueError):
            consume(name, tmp_path)
    assert len(wire.calls) == (4 if bad_kind == 'interrupted' else 1)
    assert all(size > 0 and size <= 65_536 for body in wire.bodies for size in body.requests)
    assert sum(body.delivered for body in wire.bodies) <= cap + 1
    if bad_kind == 'huge':
        assert wire.bodies[0].delivered == cap + 1
    if bad_kind == 'wrong-type':
        assert wire.bodies[0].delivered == 0
    if name in {'pipeline', 'shopee'}:
        assert dest.read_bytes() == PNG


@pytest.mark.parametrize('headers', [
    {'Authorization': 'Bearer synthetic'}, {'Cookie': 'session=synthetic'},
    {'X-tts-access-token': 'synthetic'}, {'Proxy-Authorization': 'synthetic'},
    {'Host': 'internal.invalid'}, {'Referer': 'https://source.invalid/?token=synthetic'},
])
def test_credential_headers_rejected_before_first_hop(wire, headers):
    from core.resource_download import read_resource
    request = urllib.request.Request(START, headers=headers)
    with pytest.raises(ValueError):
        read_resource(request, source='sourcing_image', timeout=2)
    assert wire.calls == []


@pytest.mark.parametrize('url', [
    'ftp://source.invalid/a', 'https://localhost/a', 'https://[::1]/a',
    'https://2130706433/a', 'https://user:pass@source.invalid/a',
    'https://source.invalid:1234/a', 'https://source.invalid/a?access_token=synthetic',
    'https://source.invalid/a?api_key=synthetic',
])
def test_initial_url_rejection_is_before_consumer_http(wire, monkeypatch, tmp_path, url):
    if '2130706433' in url:
        monkeypatch.setattr(socket, 'getaddrinfo', lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, '', ('127.0.0.1', 443))])
    with pytest.raises(ValueError):
        pipeline._download_url(url, tmp_path / 'invalid.png')
    assert wire.calls == []


@pytest.mark.parametrize('addresses', [['10.1.2.3'], ['93.184.216.34', '169.254.169.254'], ['::ffff:127.0.0.1']])
def test_dns_targets_are_checked_on_redirect(wire, monkeypatch, tmp_path, addresses):
    def resolve(host, port, **kwargs):
        ips = addresses if host == 'cdn.invalid' else ['93.184.216.34']
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, '', (ip, port)) for ip in ips]
    monkeypatch.setattr(socket, 'getaddrinfo', resolve)
    wire.queue.append(redirect(FINAL))
    with pytest.raises(ValueError):
        pipeline._download_url(START, tmp_path / 'image.png')
    assert len(wire.calls) == 1


@pytest.mark.parametrize('name', ['pipeline', 'pdf', 'shopee'])
def test_signed_source_query_preserved_exactly_on_same_origin(wire, tmp_path, name):
    url = START + '?x-signature=synthetic%2Bvalue&x-expires=123&width=800'
    target = '/final?x-signature=synthetic%2Bnext&x-expires=456'
    wire.queue.extend([redirect(target), ok()])
    assert consume(name, tmp_path, url) is not None
    assert [call['url'] for call in wire.calls] == [url, 'https://source.invalid' + target]


def test_signed_query_cannot_move_cross_origin_and_html_cannot_opt_in(wire, tmp_path):
    wire.queue.append(redirect(FINAL + '?x-signature=synthetic'))
    with pytest.raises(ValueError):
        pipeline._download_url(START + '?x-signature=synthetic', tmp_path / 'image.png')
    assert len(wire.calls) == 1
    with pytest.raises(ValueError):
        scrape_1688._fetch(START + '?signature=synthetic')
    assert len(wire.calls) == 1


def test_signed_origin_can_redirect_to_unsigned_cdn_without_forwarding_query_or_referer(wire, tmp_path):
    wire.queue.extend([redirect(FINAL), ok()])
    pipeline._download_url(START + '?signature=synthetic', tmp_path / 'image.png')
    assert wire.calls[-1]['url'] == FINAL
    assert all('synthetic' not in str(call['headers']) for call in wire.calls)


def test_real_proxy_handler_and_tls_survive_redirect_and_retry(wire, monkeypatch, tmp_path):
    monkeypatch.setattr(urllib.request, 'getproxies', lambda: {'https': 'http://proxy.fixture:3128'})
    monkeypatch.setattr(urllib.request, 'proxy_bypass', lambda host: False)
    wire.queue.extend([redirect(FINAL), TimeoutError(), ok()])
    pipeline._download_url(START, tmp_path / 'image.png')
    assert [(call['host'], call['tunnel']) for call in wire.calls] == [('proxy.fixture:3128', 'source.invalid'), ('proxy.fixture:3128', 'cdn.invalid'), ('proxy.fixture:3128', 'cdn.invalid')]
    assert all(call['verify_mode'] == ssl.CERT_REQUIRED and call['check_hostname'] for call in wire.calls)


def test_certificate_failure_is_terminal_and_safe_to_display(wire, tmp_path):
    wire.queue.append(ssl.SSLCertVerificationError('synthetic certificate secret=hidden'))
    with pytest.raises(ValueError) as caught:
        pipeline._download_url(START + '?signature=synthetic', tmp_path / 'image.png')
    assert 'synthetic' not in str(caught.value) and 'hidden' not in str(caught.value)
    assert len(wire.calls) == 1


@pytest.mark.parametrize('headers', [{'Content-Length': '10000001'}, {'Content-Encoding': 'gzip'}])
def test_oversized_header_and_compression_reject_before_reading(wire, tmp_path, headers):
    wire.queue.append(ok(**headers))
    with pytest.raises(ValueError):
        publish._download_image(START, tmp_path / 'image.png')
    assert wire.bodies[0].delivered == 0


def test_partial_attempt_bytes_share_total_budget(wire, tmp_path):
    class Partial(Body):
        def read(self, size=-1):
            if self.tell() == len(self.getvalue()):
                raise http.client.IncompleteRead(b'partial', 100)
            return super().read(size)
    wire.queue.extend([ok(Partial(b'x' * 6_000_000)), ok(b'x' * 6_000_000)])
    dest = tmp_path / 'old.png'
    dest.write_bytes(PNG)
    with pytest.raises(ValueError):
        publish._download_image(START, dest)
    assert len(wire.calls) == 2
    assert sum(body.delivered for body in wire.bodies) + len(b'partial') == 10_000_001
    assert dest.read_bytes() == PNG


@pytest.mark.parametrize('bad', [PNG[:-12], PNG[:40]])
def test_real_decode_rejects_truncated_png_even_with_image_mime(wire, tmp_path, bad):
    wire.queue.append(ok(bad))
    with pytest.raises(ValueError):
        pipeline._download_url(START, tmp_path / 'image.png')
    assert not (tmp_path / 'image.png').exists()


def test_atomic_replace_failure_keeps_old_image_and_removes_only_own_temp(wire, monkeypatch, tmp_path):
    from core import resource_download
    dest = tmp_path / 'existing.png'
    dest.write_bytes(b'old verified artifact')
    unrelated = tmp_path / '.unrelated.part'
    unrelated.write_bytes(b'preserve')
    wire.queue.append(ok())
    def denied_replace(*args):
        raise PermissionError('synthetic locked destination')
    monkeypatch.setattr(resource_download.os, 'replace', denied_replace)
    with pytest.raises(PermissionError):
        publish._download_image(START, dest)
    assert dest.read_bytes() == b'old verified artifact'
    assert list(tmp_path.glob('*.part')) == [unrelated]


def test_actual_download_images_keeps_failed_old_artifact_out_of_success_list(wire, monkeypatch, tmp_path, record_property):
    monkeypatch.setattr(pipeline, 'ROOT', tmp_path)
    monkeypatch.setattr(pipeline, 'SOURCING_DIR', tmp_path / 'sourcing')
    base = tmp_path / 'sourcing' / 'synthetic-offer' / 'raw'
    base.mkdir(parents=True)
    old = base / 'main_02.jpg'
    old.write_bytes(PNG)
    wire.queue.extend([redirect(FINAL), ok(), ok(HTML, 'text/html')])
    result = pipeline.download_images('synthetic-offer', {'images': {'main': [START, FINAL]}})
    actual = [(tmp_path / value).resolve() for value in result['raw_main']]
    assert all(path.is_relative_to(tmp_path.resolve()) for path in actual)
    assert actual == [(base / 'main_01.jpg').resolve()]
    assert old.read_bytes() == PNG
    assert (base / 'main_01.jpg').read_bytes() == PNG
    record_property('raw_main', result['raw_main'])
    record_property('output_sha256', hashlib.sha256(PNG).hexdigest())


def test_actual_html_detail_parser_receives_redirected_html(wire):
    wire.queue.extend([redirect('/detail'), ok(HTML, 'text/html; charset=utf-8')])
    result = scrape_1688.parse_html('<html>"offerDetail": {"detailUrl": "' + START + '"}</html>', offer_id='synthetic-offer')
    assert result['images']['detail'] == ['https://img.alicdn.com/synthetic.png']


def test_actual_pdf_embeds_downloaded_image(wire, monkeypatch, tmp_path, record_property):
    monkeypatch.setattr(pdf_export, '_ensure_font', lambda: 'Helvetica')
    wire.queue.extend([redirect(FINAL), ok()])
    result = pdf_export.build_catalog_pdf([{'match_key': 'fixture', 'product_name': 'Synthetic', 'image_url': START}])
    assert result.startswith(b'%PDF-')
    assert b'/Subtype /Image' in result, 'a PDF with a missing-image label is not success'
    artifact = tmp_path / 'resource-catalog.pdf'
    artifact.write_bytes(result)
    record_property('pdf_path', str(artifact))
    record_property('pdf_sha256', hashlib.sha256(result).hexdigest())


@pytest.mark.parametrize('entry', ['_upload_images', '_upload_images_exact'])
def test_actual_upload_preconsumer_decodes_exact_downloaded_bytes(wire, monkeypatch, entry, record_property):
    uploaded = []
    def upload(path, *, scene):
        raw = Path(path).read_bytes()
        with Image.open(io.BytesIO(raw)) as picture:
            picture.load()
            assert picture.size == (8, 6)
        uploaded.append({'sha256': hashlib.sha256(raw).hexdigest(), 'scene': scene})
        return {'image_info': {'image_id': 'synthetic-image'}}
    monkeypatch.setattr(publish, 'upload_image', upload)
    wire.queue.extend([redirect(FINAL), ok()])
    assert getattr(publish, entry)([START]) == ['synthetic-image']
    assert uploaded == [{'sha256': hashlib.sha256(PNG).hexdigest(), 'scene': 'normal'}]
    record_property('synthetic_upload_preconsumer', uploaded)


def test_legacy_public_http_can_upgrade_but_cannot_downgrade_again(wire, tmp_path):
    wire.queue.extend([redirect(FINAL), ok()])
    pipeline._download_url('http://source.invalid/start', tmp_path / 'valid.png')
    assert (tmp_path / 'valid.png').read_bytes() == PNG
    assert wire.calls[0]['verify_mode'] is None
    assert wire.calls[1]['verify_mode'] == 2
    wire.queue.extend([redirect(FINAL), redirect('http://source.invalid/end')])
    with pytest.raises(ValueError):
        pipeline._download_url('http://source.invalid/start', tmp_path / 'invalid.png')
    assert len(wire.calls) == 4


@pytest.mark.parametrize('status', [204, 206, 400, 404, 429, 500])
def test_non_200_never_reaches_uploaded_image(wire, tmp_path, status):
    wire.queue.append((status, PNG, {'Content-Type': 'image/png'}))
    with pytest.raises(ValueError):
        publish._download_image(START, tmp_path / 'image.png')
    assert len(wire.calls) == 1
    assert wire.bodies[0].delivered == 0


@pytest.mark.parametrize('case', ['empty', 'mime-mismatch', 'truncated-jpeg', 'length-mismatch'])
def test_complete_image_content_contract(wire, tmp_path, case):
    if case == 'empty':
        wire.queue.append(ok(b''))
    elif case == 'mime-mismatch':
        wire.queue.append(ok(PNG, 'image/jpeg'))
    elif case == 'truncated-jpeg':
        buf = io.BytesIO()
        Image.new('RGB', (100, 100)).save(buf, format='JPEG')
        wire.queue.append(ok(buf.getvalue()[:-20], 'image/jpeg'))
    else:
        wire.queue.extend([ok(PNG, **{'Content-Length': str(len(PNG) + 10)}) for _ in range(4)])
    with pytest.raises(ValueError):
        pipeline._download_url(START, tmp_path / 'image.png')
    assert not (tmp_path / 'image.png').exists()


@pytest.mark.parametrize('method,data', [('POST', None), ('GET', b'body'), ('HEAD', None)])
def test_resource_helper_cannot_enable_provider_writes(wire, method, data):
    from core.resource_download import read_resource
    with pytest.raises(ValueError):
        read_resource(urllib.request.Request(START, method=method, data=data), source='shopee_image', timeout=2)
    assert wire.calls == []


@pytest.mark.parametrize('target', ['https://user:password@cdn.invalid/final', FINAL])
def test_http_error_close_failure_is_safe_and_preserves_redirect_failure(wire, tmp_path, target):
    from core.resource_download import ResourceDownloadError
    class BadClose(Body):
        def close(self):
            if not self.closed:
                super().close()
                raise RuntimeError('synthetic-remote-secret-in-close')
    wire.queue.append((302, BadClose(b''), {'Location': target}))
    wire.queue.append(ok())
    with pytest.raises(ResourceDownloadError) as caught:
        pipeline._download_url(START, tmp_path / 'image.png')
    assert 'synthetic-remote-secret' not in str(caught.value)
    if target != FINAL:
        assert 'target or query' in str(caught.value)
    assert len(wire.calls) == 1
    assert not (tmp_path / 'image.png').exists()


@pytest.mark.parametrize('replace_fails', [True, False])
def test_atomic_cleanup_failure_does_not_mask_original_or_report_successful_replace_as_failed(wire, monkeypatch, tmp_path, replace_fails):
    from core import resource_download
    dest = tmp_path / 'existing.png'
    dest.write_bytes(b'old verified resource')
    cleanup_calls = []
    original_error = PermissionError('original replacement failure')
    if replace_fails:
        def denied_replace(*args):
            raise original_error
        monkeypatch.setattr(resource_download.os, 'replace', denied_replace)
    original_unlink = Path.unlink
    def denied_cleanup(path, *args, **kwargs):
        if path.parent == tmp_path and path.suffix == '.part':
            cleanup_calls.append(path)
            raise PermissionError('secondary cleanup failure')
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'unlink', denied_cleanup)
    wire.queue.append(ok())
    if replace_fails:
        with pytest.raises(PermissionError) as caught:
            publish._download_image(START, dest)
        assert caught.value is original_error
        assert dest.read_bytes() == b'old verified resource'
        assert len(cleanup_calls) == 1
        assert cleanup_calls[0].is_file(), 'the owned orphan is retained when cleanup fails'
    else:
        assert publish._download_image(START, dest) == dest
        assert dest.read_bytes() == PNG
        assert cleanup_calls == []
