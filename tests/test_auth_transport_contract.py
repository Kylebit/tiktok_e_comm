"""OAuth and refresh contracts using only synthetic credentials and storage."""
from __future__ import annotations

import builtins
import importlib.util
import io
import json
from pathlib import Path
import socket
import sqlite3
import ssl
import subprocess
import sys
from types import ModuleType, SimpleNamespace
import urllib.error
import webbrowser

import pytest


@pytest.fixture
def auth_fixture(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("real network, browser, credential file or database access is forbidden")
    for owner, name in ((socket.socket, "connect"), (socket.socket, "bind"), (socket, "getaddrinfo"),
                        (sqlite3, "connect"), (subprocess, "run"), (webbrowser, "open")):
        monkeypatch.setattr(owner, name, forbidden)
    for owner, name in ((builtins, "open"), (io, "open")):
        original = getattr(owner, name)
        def guarded(file, *args, _original=original, **kwargs):
            if isinstance(file, (str, bytes, Path)):
                filename = Path(file).name.lower()
                if filename in {"settings.json", "tiktok_tokens.json"} or filename.endswith(".db"):
                    forbidden()
            return _original(file, *args, **kwargs)
        monkeypatch.setattr(owner, name, guarded)
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(sys, "path", sys.path.copy())  # OAuth module inserts its own candidate root.
    package = ModuleType("core")
    package.__path__ = [str(root / "core")]
    monkeypatch.setitem(sys.modules, "core", package)
    state = SimpleNamespace(token={"access_token": "fixture-old-access", "refresh_token": "fixture-old-refresh",
                                  "access_token_expire_in": 900, "refresh_token_expire_in": 5000},
                            saved=[], sends=[], sleeps=[], policy=[], settings_reads=0)
    config = ModuleType("core.config")
    config.ROOT = root
    config.get = lambda key, default=None: default
    config.settings_base_dir = forbidden
    def settings():
        state.settings_reads += 1
        return {"app_key": "fixture-key", "app_secret": "fixture-secret"}
    config.load_settings = settings
    monkeypatch.setitem(sys.modules, "core.config", config)

    def load(name, relative):
        spec = importlib.util.spec_from_file_location(name, root / relative)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
        return module

    transport = load("core.http_retry", "core/http_retry.py")
    monkeypatch.setattr(transport.time, "sleep", state.sleeps.append)
    auth = load("core.auth", "core/auth.py")
    monkeypatch.setattr(auth.time, "time", lambda: 1000)
    auth.load_token = lambda: state.token.copy()
    auth.save_token = lambda token: state.saved.append(token.copy())
    actual = auth.urlopen_retry
    def policy(req, **kwargs):
        state.policy.append(kwargs)
        return actual(req, **kwargs)
    auth.urlopen_retry = policy
    oauth = load("fixture_tiktok_auth", "tiktok_auth.py")
    oauth.HTTPServer = forbidden
    return SimpleNamespace(auth=auth, oauth=oauth, transport=transport, state=state)


def complete_response():
    return {"code": 0, "data": {"access_token": "fixture-new-access", "refresh_token": "fixture-new-refresh",
                                "access_token_expire_in": 4000, "refresh_token_expire_in": 6000,
                                "open_id": "fixture-id", "seller_name": "fixture-seller", "authorized_shops": []}}


def respond(fixture, monkeypatch, result):
    def send(req, **kwargs):
        fixture.state.sends.append((req.get_method(), kwargs))
        return io.BytesIO(json.dumps(result).encode())
    monkeypatch.setattr(fixture.transport, "_urlopen_once", send)
    monkeypatch.setattr(fixture.oauth.urllib.request, "urlopen", send)


def test_standalone_oauth_context_requires_tls_verification(auth_fixture):
    assert auth_fixture.oauth.ssl_ctx.verify_mode == ssl.CERT_REQUIRED
    assert auth_fixture.oauth.ssl_ctx.check_hostname is True


def test_unknown_get_refresh_outcome_is_not_replayed(auth_fixture, monkeypatch):
    fixture = auth_fixture
    failure = TimeoutError("fixture unknown refresh outcome")
    def fail(req, **kwargs):
        fixture.state.sends.append(req.get_method())
        raise failure
    monkeypatch.setattr(fixture.transport, "_urlopen_once", fail)
    with pytest.raises(TimeoutError) as caught:
        fixture.auth.refresh_access_token()
    assert caught.value is failure
    assert fixture.state.sends == ["GET"]
    assert fixture.state.saved == [] and fixture.state.sleeps == []


@pytest.mark.parametrize("data", ({"access_token": "", "access_token_expire_in": 4000}, {"access_token": "fixture-new-access"}))
def test_incomplete_refresh_response_is_not_saved(auth_fixture, monkeypatch, data):
    respond(auth_fixture, monkeypatch, {"code": 0, "data": data})
    with pytest.raises((RuntimeError, KeyError, TypeError, ValueError)):
        auth_fixture.auth.refresh_access_token()
    assert auth_fixture.state.saved == []


@pytest.mark.parametrize("refresh", (42, None, " "))
def test_explicit_damaged_refresh_token_is_not_saved(auth_fixture, monkeypatch, refresh):
    response = complete_response()
    response["data"]["refresh_token"] = refresh
    respond(auth_fixture, monkeypatch, response)
    with pytest.raises((RuntimeError, KeyError, TypeError, ValueError)):
        auth_fixture.auth.refresh_access_token()
    assert auth_fixture.state.saved == []


def test_complete_refresh_saves_once_with_explicit_single_attempt(auth_fixture, monkeypatch):
    fixture = auth_fixture
    respond(fixture, monkeypatch, complete_response())
    updated = fixture.auth.refresh_access_token()
    assert fixture.state.saved == [updated]
    assert updated["access_token"] == "fixture-new-access"
    assert updated["refresh_token"] == "fixture-new-refresh"
    assert updated["access_token_expire_in"] == 4000 and updated["saved_at"] == 1000
    assert updated["open_id"] == "fixture-id" and updated["seller_name"] == "fixture-seller"
    assert len(fixture.state.sends) == 1
    assert fixture.state.policy == [{"timeout": 30, "context": fixture.auth.SSL_CTX, "attempts": 1, "allow_curl_fallback": False}]


def test_optional_refresh_fields_keep_existing_fallback(auth_fixture, monkeypatch):
    fixture = auth_fixture
    respond(fixture, monkeypatch, {"code": 0, "data": {"access_token": "fixture-new-access", "access_token_expire_in": "4000"}})
    updated = fixture.auth.refresh_access_token()
    assert updated["refresh_token"] == "fixture-old-refresh"
    assert updated["refresh_token_expire_in"] == 5000
    assert fixture.state.saved == [updated]


def test_known_valid_access_token_needs_no_refresh_or_settings(auth_fixture, monkeypatch):
    fixture = auth_fixture
    fixture.state.token["access_token_expire_in"] = 4000
    monkeypatch.setattr(fixture.transport, "_urlopen_once", lambda *a, **k: pytest.fail("unneeded refresh"))
    assert fixture.auth.refresh_access_token() == fixture.state.token
    assert fixture.state.settings_reads == 0 and fixture.state.saved == []


@pytest.mark.parametrize("refresh,expiry", ((None, 5000), ("fixture-old-refresh", 999)))
def test_unusable_refresh_credentials_fail_without_transport(auth_fixture, monkeypatch, refresh, expiry):
    fixture = auth_fixture
    fixture.state.token.update(refresh_token=refresh, refresh_token_expire_in=expiry)
    monkeypatch.setattr(fixture.transport, "_urlopen_once", lambda *a, **k: pytest.fail("invalid refresh sent"))
    with pytest.raises(RuntimeError, match="refresh_token"):
        fixture.auth.refresh_access_token()
    assert fixture.state.saved == [] and fixture.state.settings_reads == 0


@pytest.mark.parametrize("failure", (ConnectionResetError("fixture reset"), urllib.error.URLError(ssl.SSLEOFError("fixture EOF")),
                                     urllib.error.URLError(ssl.SSLCertVerificationError("fixture certificate")),
                                     urllib.error.HTTPError("https://fixture.invalid/", 401, "fixture", None, None)))
def test_refresh_transport_failures_preserve_error_without_save_or_replay(auth_fixture, monkeypatch, failure):
    fixture = auth_fixture
    def fail(*args, **kwargs):
        fixture.state.sends.append(True)
        raise failure
    monkeypatch.setattr(fixture.transport, "_urlopen_once", fail)
    with pytest.raises(type(failure)) as caught:
        fixture.auth.refresh_access_token()
    assert caught.value is failure
    assert fixture.state.sends == [True] and fixture.state.saved == [] and fixture.state.sleeps == []


@pytest.mark.parametrize("result", ({"code": 1, "message": "fixture denied"}, {"code": 0}, {"code": 0, "data": {}},
                                    {"code": 0, "data": {"access_token": "fixture-new-access", "access_token_expire_in": "invalid"}}))
def test_failed_or_malformed_refresh_response_does_not_save(auth_fixture, monkeypatch, result):
    respond(auth_fixture, monkeypatch, result)
    with pytest.raises((RuntimeError, KeyError, TypeError, ValueError)):
        auth_fixture.auth.refresh_access_token()
    assert auth_fixture.state.saved == [] and len(auth_fixture.state.sends) == 1


def test_standalone_oauth_keeps_single_exchange_and_secure_context(auth_fixture, monkeypatch):
    fixture = auth_fixture
    response = complete_response()
    respond(fixture, monkeypatch, response)
    assert fixture.oauth.get_token("fixture-code", {"app_key": "fixture-key", "app_secret": "fixture-secret"}) == response
    assert fixture.state.sends == [("GET", {"timeout": 15, "context": fixture.oauth.ssl_ctx})]
    assert fixture.oauth.ssl_ctx.check_hostname and fixture.oauth.ssl_ctx.verify_mode == ssl.CERT_REQUIRED
    assert fixture.state.saved == [] and fixture.state.settings_reads == 0
