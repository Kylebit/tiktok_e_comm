from __future__ import annotations

from types import SimpleNamespace
from pathlib import Path
import io
import os
import shutil
import socket
import ssl
import subprocess
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
import urllib.error
import urllib.request

import pytest

from core import http_retry

ORIGINAL_RUN = subprocess.run


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    allowed = set()
    connect = socket.socket.connect
    getaddrinfo = socket.getaddrinfo

    def guarded_connect(sock, address):
        assert address in allowed, "non-fixture socket connection attempted"
        return connect(sock, address)

    def guarded_dns(host, *args, **kwargs):
        assert host in {"localhost", "127.0.0.1", "::1"}, "external DNS attempted"
        return getaddrinfo(host, *args, **kwargs)

    def no_process(*args, **kwargs):
        raise AssertionError("unexpected real subprocess; curl must be simulated")

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_dns)
    monkeypatch.setattr(http_retry.subprocess, "run", no_process)
    monkeypatch.setattr(http_retry.urllib.request, "getproxies", lambda: {})
    monkeypatch.setattr(http_retry.urllib.request, "proxy_bypass", lambda host: False)
    for key in ("SSL_CERT_FILE", "SSL_CERT_DIR", "CURL_CA_BUNDLE"):
        monkeypatch.delenv(key, raising=False)
    return allowed


def test_default_tls_verifies_certificate_and_hostname():
    assert http_retry.DEFAULT_SSL_CTX.verify_mode == ssl.CERT_REQUIRED
    assert http_retry.DEFAULT_SSL_CTX.check_hostname is True


def test_unknown_post_outcome_is_not_automatically_replayed(monkeypatch):
    calls = []
    failure = TimeoutError("synthetic unknown submission outcome")

    def transport(*args, **kwargs):
        calls.append(True)
        raise failure

    monkeypatch.setattr(http_retry, "_urlopen_once", transport)
    monkeypatch.setattr(http_retry.time, "sleep", lambda _: None)
    req = urllib.request.Request("https://fixture.invalid/write", data=b"fixture", method="POST")
    with pytest.raises(TimeoutError) as caught:
        http_retry.urlopen(req)
    assert caught.value is failure
    assert len(calls) == 1


@pytest.mark.parametrize("raw", (b"fixture-body", b"fixture-body\n__CURL_HTTP_CODE__:invalid"))
def test_curl_without_a_valid_status_does_not_fabricate_success(monkeypatch, raw):
    monkeypatch.setattr(http_retry.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=raw, stderr=b""))
    req = urllib.request.Request("https://fixture.invalid/read")
    with pytest.raises(urllib.error.URLError):
        http_retry._curl_urlopen(req, 1)


@pytest.mark.parametrize("method", ("POST", "PUT", "PATCH", "DELETE", "OPTIONS", "CUSTOM"))
@pytest.mark.parametrize("failure", (TimeoutError("unknown"), ConnectionResetError("unknown"), urllib.error.URLError(ssl.SSLEOFError("EOF"))))
def test_non_read_methods_send_once_even_with_idempotency_key(monkeypatch, method, failure):
    sends, sleeps, curls = [], [], []
    def fail(*args, **kwargs):
        sends.append(True)
        raise failure
    monkeypatch.setattr(http_retry, "_urlopen_once", fail)
    monkeypatch.setattr(http_retry.time, "sleep", sleeps.append)
    monkeypatch.setattr(http_retry, "_curl_urlopen", lambda *a, **k: curls.append(True))
    req = urllib.request.Request("https://fixture.invalid/write", data=b"fixture", method=method,
                                 headers={"Idempotency-Key": "fixture-key"})
    with pytest.raises(type(failure)) as caught:
        http_retry.urlopen(req, attempts=4)
    assert caught.value is failure
    assert sends == [True] and sleeps == [] and curls == []


def test_get_with_body_is_not_replayed(monkeypatch):
    sends = []
    def fail(*args, **kwargs):
        sends.append(True)
        raise ConnectionResetError("unknown")
    monkeypatch.setattr(http_retry, "_urlopen_once", fail)
    req = urllib.request.Request("https://fixture.invalid/read", data=b"fixture", method="GET")
    with pytest.raises(ConnectionResetError):
        http_retry.urlopen(req)
    assert sends == [True]


@pytest.mark.parametrize("method", ("GET", "HEAD"))
def test_safe_reads_keep_timeout_and_backoff_retries(monkeypatch, method):
    sends, sleeps = [], []
    response = object()
    def transport(req, **kwargs):
        sends.append(kwargs)
        if len(sends) < 3:
            raise urllib.error.URLError(TimeoutError())
        return response
    monkeypatch.setattr(http_retry, "_urlopen_once", transport)
    monkeypatch.setattr(http_retry.time, "sleep", sleeps.append)
    req = urllib.request.Request("https://fixture.invalid/read", method=method)
    assert http_retry.urlopen(req, timeout=0.25, backoff=(0.1, 0.2), attempts=4) is response
    assert len(sends) == 3 and sleeps == [0.1, 0.2]
    assert all(item == {"timeout": 0.25, "context": http_retry.DEFAULT_SSL_CTX} for item in sends)


@pytest.mark.parametrize("attempts", (1, 2, 4))
def test_curl_uses_final_read_attempt_without_expanding_budget(monkeypatch, attempts):
    calls, sleeps = [], []
    failure = ConnectionResetError("synthetic")
    def transport(*args, **kwargs):
        calls.append("urllib")
        raise failure
    def curl(*args, **kwargs):
        calls.append("curl")
        return "fixture response"
    monkeypatch.setattr(http_retry, "_urlopen_once", transport)
    monkeypatch.setattr(http_retry.time, "sleep", sleeps.append)
    monkeypatch.setattr(http_retry, "_curl_urlopen", curl)
    req = urllib.request.Request("https://fixture.invalid/read")
    if attempts == 1:
        with pytest.raises(ConnectionResetError) as caught:
            http_retry.urlopen(req, attempts=attempts, backoff=(0,))
        assert caught.value is failure
    else:
        assert http_retry.urlopen(req, attempts=attempts, backoff=(0,)) == "fixture response"
        assert calls[-1] == "curl"
    assert len(calls) == attempts
    assert len(sleeps) == attempts - 1


@pytest.mark.parametrize("failure", (
    ssl.SSLCertVerificationError("certificate verify failed"),
    urllib.error.URLError(ssl.SSLCertVerificationError("untrusted certificate")),
    urllib.error.URLError("[SSL: CERTIFICATE_VERIFY_FAILED] fixture"),
))
def test_certificate_failure_terminates_without_retry_or_curl(monkeypatch, failure):
    sends, sleeps, curls = [], [], []
    def fail(*args, **kwargs):
        sends.append(True)
        raise failure
    monkeypatch.setattr(http_retry, "_urlopen_once", fail)
    monkeypatch.setattr(http_retry.time, "sleep", sleeps.append)
    monkeypatch.setattr(http_retry, "_curl_urlopen", lambda *a, **k: curls.append(True))
    with pytest.raises(type(failure)) as caught:
        http_retry.urlopen(urllib.request.Request("https://fixture.invalid/read"))
    assert caught.value is failure
    assert sends == [True] and sleeps == [] and curls == []
    assert not http_retry._retryable(failure)
    assert not http_retry._curl_fallback_error(failure)


@pytest.mark.parametrize("method", ("GET", "POST"))
def test_http_error_is_preserved_without_retry(monkeypatch, method):
    calls = []
    failure = urllib.error.HTTPError("https://fixture.invalid/", 429, "fixture", None, io.BytesIO(b"fixture"))
    def fail(*args, **kwargs):
        calls.append(True)
        raise failure
    monkeypatch.setattr(http_retry, "_urlopen_once", fail)
    with pytest.raises(urllib.error.HTTPError) as caught:
        http_retry.urlopen(urllib.request.Request(failure.url, method=method))
    assert caught.value is failure
    assert caught.value.read() == b"fixture"
    assert calls == [True]


@pytest.mark.parametrize("cert_none", (False, True))
def test_insecure_context_is_rejected_before_transport(monkeypatch, cert_none):
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    if cert_none:
        ctx.verify_mode = ssl.CERT_NONE
    monkeypatch.setattr(http_retry, "_urlopen_once", lambda *a, **k: pytest.fail("insecure transport attempted"))
    with pytest.raises(ValueError, match="certificate and hostname verification"):
        http_retry.urlopen(urllib.request.Request("https://fixture.invalid/"), context=ctx)


@pytest.mark.parametrize("use_shared_context", (False, True))
def test_explicit_secure_context_is_preserved_and_never_downgraded_to_curl(monkeypatch, use_shared_context):
    ctx = http_retry.DEFAULT_SSL_CTX if use_shared_context else ssl.create_default_context()
    seen = []
    failure = ssl.SSLEOFError("synthetic EOF")
    def fail(req, **kwargs):
        seen.append(kwargs["context"])
        raise failure
    monkeypatch.setattr(http_retry, "_urlopen_once", fail)
    monkeypatch.setattr(http_retry.time, "sleep", lambda _: None)
    with pytest.raises(ssl.SSLEOFError) as caught:
        http_retry.urlopen(urllib.request.Request("https://fixture.invalid/"), context=ctx, attempts=3)
    assert caught.value is failure
    assert seen == [ctx, ctx, ctx]


@pytest.mark.parametrize("kwargs", (
    {"attempts": 0}, {"attempts": -1}, {"attempts": 1.5}, {"attempts": True},
    {"timeout": 0}, {"timeout": -1}, {"timeout": float("inf")}, {"timeout": float("nan")}, {"timeout": True},
    {"backoff": ()}, {"backoff": (-1,)}, {"backoff": (float("inf"),)}, {"backoff": (True,)},
    {"backoff": "123"}, {"allow_curl_fallback": "yes"},
))
def test_invalid_parameters_fail_before_sending(monkeypatch, kwargs):
    monkeypatch.setattr(http_retry, "_urlopen_once", lambda *a, **k: pytest.fail("invalid parameters sent"))
    with pytest.raises(ValueError):
        http_retry.urlopen(urllib.request.Request("https://fixture.invalid/"), **kwargs)


@pytest.mark.parametrize("proxy,bypass,expected", (("http://proxy.fixture:3128", False, "http://proxy.fixture:3128"),
                                                 ("http://proxy.fixture:3128", True, ""), (None, False, "")))
@pytest.mark.parametrize("method", ("GET", "HEAD"))
def test_curl_keeps_verified_proxy_selection_and_fractional_timeout(monkeypatch, proxy, bypass, expected, method):
    seen = []
    monkeypatch.setattr(http_retry.urllib.request, "getproxies", lambda: {"https": proxy, "all": "http://must-not-invent:9999"})
    monkeypatch.setattr(http_retry.urllib.request, "proxy_bypass", lambda host: bypass)
    def run(cmd, **kwargs):
        seen.append((cmd, kwargs))
        return SimpleNamespace(returncode=0, stdout=b"fixture\n__CURL_HTTP_CODE__:200", stderr=b"")
    monkeypatch.setattr(http_retry.subprocess, "run", run)
    req = urllib.request.Request("https://fixture.invalid/", method=method, headers={"Authorization": "fixture-only"})
    assert http_retry._curl_urlopen(req, 0.25).status == 200
    cmd, options = seen[0]
    assert cmd[:2] == ["curl", "-q"]
    assert cmd[cmd.index("--proxy") + 1] == expected
    assert cmd[cmd.index("--noproxy") + 1] == ""
    assert "-k" not in cmd and "--insecure" not in cmd and "--retry" not in cmd
    assert cmd[cmd.index("-m") + 1] == "0.25"
    assert options["timeout"] == 5.25
    assert ("--head" in cmd) == (method == "HEAD")


@pytest.mark.parametrize("raw_status", (b"000", b"100", b"199", b"600", b"999", b"200:extra", b" 200", b"200\n"))
def test_curl_rejects_invalid_final_status(monkeypatch, raw_status):
    monkeypatch.setattr(http_retry.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=b"fixture\n__CURL_HTTP_CODE__:" + raw_status, stderr=b""))
    with pytest.raises(urllib.error.URLError, match="valid final HTTP status"):
        http_retry._curl_urlopen(urllib.request.Request("https://fixture.invalid/"), 1)


def test_curl_http_error_keeps_status_and_payload(monkeypatch):
    monkeypatch.setattr(http_retry.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout=b"fixture\n__CURL_HTTP_CODE__:503", stderr=b""))
    with pytest.raises(urllib.error.HTTPError) as caught:
        http_retry._curl_urlopen(urllib.request.Request("https://fixture.invalid/"), 1)
    assert caught.value.code == 503 and caught.value.read() == b"fixture"


@pytest.mark.parametrize("failure", ("exit", "timeout"))
def test_curl_failures_do_not_echo_headers_url_or_body(monkeypatch, failure):
    secret = "fixture-sensitive-marker"
    def run(cmd, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired(cmd, 1, stderr=secret.encode())
        return SimpleNamespace(returncode=60, stdout=b"", stderr=secret.encode())
    monkeypatch.setattr(http_retry.subprocess, "run", run)
    request = urllib.request.Request("https://fixture.invalid/?fixture=" + secret, headers={"Authorization": secret})
    with pytest.raises(urllib.error.URLError) as caught:
        http_retry._curl_urlopen(request, 1)
    assert secret not in str(caught.value)
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("boundary", ("disabled", "ca-environment", "explicit-request-proxy"))
def test_fallback_preserves_explicit_network_boundaries(monkeypatch, boundary):
    req = urllib.request.Request("https://fixture.invalid/read")
    options = {"attempts": 2, "backoff": (0,)}
    if boundary == "disabled":
        options["allow_curl_fallback"] = False
    elif boundary == "ca-environment":
        monkeypatch.setenv("SSL_CERT_FILE", "fixture-custom-ca.pem")
    else:
        req.set_proxy("proxy.fixture:3128", "http")
    seen = []
    def fail(*args, **kwargs):
        seen.append(True)
        raise ConnectionResetError("synthetic reset")
    monkeypatch.setattr(http_retry, "_urlopen_once", fail)
    monkeypatch.setattr(http_retry.time, "sleep", lambda _: None)
    with pytest.raises(ConnectionResetError):
        http_retry.urlopen(req, **options)
    assert seen == [True, True]


@pytest.mark.parametrize("method,body", (("POST", b"fixture"), ("PUT", None), ("GET", b"fixture")))
def test_direct_curl_cannot_be_used_to_replay_a_write(method, body):
    req = urllib.request.Request("https://fixture.invalid/", method=method, data=body)
    with pytest.raises(ValueError, match="GET/HEAD"):
        http_retry._curl_urlopen(req, 1)


def test_real_loopback_self_signed_tls_is_rejected_and_custom_trust_works(tmp_path, monkeypatch, offline, request):
    git = shutil.which("git")
    openssl = shutil.which("openssl")
    if not openssl and git:
        candidate = Path(git).resolve().parents[1] / "usr/bin/openssl.exe"
        openssl = str(candidate) if candidate.is_file() else None
    if not openssl:
        pytest.skip("No existing OpenSSL executable; synthetic TLS failures are tested separately")
    cert, key = tmp_path / "fixture-cert.pem", tmp_path / "fixture-key.pem"
    result = ORIGINAL_RUN([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-keyout", str(key),
                           "-out", str(cert), "-days", "1", "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost"],
                          capture_output=True, timeout=30)
    assert result.returncode == 0, "local fixture certificate generation failed"
    reached = []
    class FixtureHandler(BaseHTTPRequestHandler):
        def do_GET(self):
            reached.append(True)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"local trusted fixture")
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert, key)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    offline.update({("127.0.0.1", server.server_port), ("::1", server.server_port, 0, 0)})
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    sends, curls = [], []
    actual = http_retry._urlopen_once
    def observed(*args, **kwargs):
        sends.append(True)
        return actual(*args, **kwargs)
    monkeypatch.setattr(http_retry, "_urlopen_once", observed)
    monkeypatch.setattr(http_retry, "_curl_urlopen", lambda *a, **k: curls.append(True))
    try:
        req = urllib.request.Request(f"https://localhost:{server.server_port}/")
        with pytest.raises(urllib.error.URLError) as caught:
            http_retry.urlopen(req, timeout=2)
        assert isinstance(caught.value.reason, ssl.SSLCertVerificationError)
        assert sends == [True] and curls == [] and reached == []
        trusted = ssl.create_default_context(cafile=str(cert))
        with http_retry.urlopen(req, timeout=2, context=trusted) as response:
            assert response.read() == b"local trusted fixture"
        assert reached == [True] and curls == []
        request.node.user_properties.extend((
            ("real_loopback_tls_handshake", True), ("self_signed_rejected", True),
            ("explicit_custom_ca_succeeded", True), ("application_requests", len(reached)),
            ("curl_fallbacks", len(curls)), ("certificate_generator", openssl),
        ))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
        assert not thread.is_alive()
