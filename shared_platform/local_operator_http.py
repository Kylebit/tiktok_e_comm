"""Explicit private local bootstrap/session transport; production default off.

The ordinary HTTP endpoint can only consume a short one-time carrier minted by
the local Windows-token producer. It never issues same-user capabilities.
"""
from __future__ import annotations

import base64
import hashlib
from http.cookies import SimpleCookie
import ipaddress
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

from shared_platform.common_offer_authority_store import CommonAuthorityBlocked, _fail
from shared_platform.local_operator_session import PrivateOperatorGrant, consume_private_browser_handoff
from shared_platform.private_final_decision_store import (
    PrivateFinalDecisionStore, PrivateFinalFakeTransport, execute_private_final_successor,
)


PREFIX = "/api/product-workspace/local-operator/"
INIT_PATH = "/local-operator-init"
_INIT_SCRIPT = """(async()=>{
  const nonce=location.hash.slice(1);
  history.replaceState(null,'',location.pathname);
  if(!/^[0-9a-f]{64}$/.test(nonce)){document.body.textContent='需要本机初始化程序打开此页面。';return;}
  try{
    const response=await fetch('/api/product-workspace/local-operator/bootstrap',{
      method:'POST',credentials:'same-origin',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({handoff:nonce})});
    if(!response.ok)throw new Error('初始化凭据已失效，请由本机程序重新初始化。');
    const result=await response.json();
    if(result.session_ready!==true)throw new Error('本机会话暂不可用。');
    if(typeof result.review_path!=='string'||!result.review_path.startsWith('/product-workspace?'))throw new Error('审核目标未连接。');
    location.replace(result.review_path);
  }catch(error){document.body.textContent=error.message;}
})();"""
INIT_HTML = ('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>本机会话初始化</title>'
             '<body>正在连接本机会话…<script>' + _INIT_SCRIPT + '</script></body></html>').encode()


class PrivateLocalReviewHttp:
    """Installed only by an explicit private launcher/fixture, never an env flag."""
    def __init__(self, store, *, fake_successor=None):
        if type(store) is not PrivateFinalDecisionStore:
            _fail("PRIVATE_LOCAL_REVIEW_STORE_REQUIRED")
        store.sessions._owner()
        if fake_successor is not None and type(fake_successor) is not PrivateFinalFakeTransport:
            _fail('PRIVATE_FINAL_FAKE_REQUIRED')
        self.store = store
        self.fake_successor = fake_successor

    def names(self, port):
        instance = hashlib.sha256(self.store.sessions.authority.marker["instance_id"].encode()).hexdigest()[:16]
        suffix = instance + "_" + str(port)
        return "orbit_operator_" + suffix, "orbit_csrf_" + suffix

    def _bound(self, handler, *, write=False):
        host, port = handler.server.server_address[:2]
        if host != "127.0.0.1" or not ipaddress.ip_address(handler.client_address[0]).is_loopback:
            _fail("LOCAL_OPERATOR_LOOPBACK_REQUIRED")
        if handler.headers.get_all("Host") != [f"127.0.0.1:{port}"]:
            _fail("LOCAL_OPERATOR_HOST_INVALID")
        if write and handler.headers.get_all("Origin") != [f"http://127.0.0.1:{port}"]:
            _fail("LOCAL_OPERATOR_ORIGIN_INVALID")
        return port

    def _grant(self, handler, port):
        raw = handler.headers.get_all("Cookie") or []
        csrf = handler.headers.get_all("X-Orbit-CSRF-Token") or []
        if len(raw) != 1 or len(csrf) != 1:
            _fail("LOCAL_OPERATOR_SESSION_REQUIRED")
        cookies = SimpleCookie()
        try:
            cookies.load(raw[0])
            name, _ = self.names(port)
            value = cookies[name].value
            session_id, capability = value.split(".")
        except (KeyError, ValueError):
            _fail("LOCAL_OPERATOR_SESSION_REQUIRED")
        grant = PrivateOperatorGrant(session_id, capability, csrf[0])
        with self.store._transaction(readonly=True) as db:
            self.store.sessions.authenticate(db, grant)
        return grant

    def _body(self, handler):
        lengths = handler.headers.get_all("Content-Length") or []
        if (handler.headers.get("Transfer-Encoding") or len(lengths) != 1
                or not lengths[0].isdigit() or not 0 < int(lengths[0]) <= 4096
                or handler.headers.get_all("Content-Type") != ["application/json"]):
            _fail("LOCAL_OPERATOR_BODY_INVALID")
        prior_timeout = handler.connection.gettimeout()
        try:
            handler.connection.settimeout(3)
            raw = handler.rfile.read(int(lengths[0]))
        finally:
            handler.connection.settimeout(prior_timeout)
        if len(raw) != int(lengths[0]):
            _fail("LOCAL_OPERATOR_BODY_INVALID")
        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    _fail("LOCAL_OPERATOR_BODY_INVALID")
                value[key] = item
            return value
        try:
            value = json.loads(raw.decode(), object_pairs_hook=unique,
                               parse_constant=lambda _: _fail("LOCAL_OPERATOR_BODY_INVALID"))
        except (UnicodeError, ValueError):
            _fail("LOCAL_OPERATOR_BODY_INVALID")
        if type(value) is not dict:
            _fail("LOCAL_OPERATOR_BODY_INVALID")
        return value

    def handle(self, handler, *, method):
        path = urlsplit(handler.path)
        if path.query or path.fragment:
            _fail("LOCAL_OPERATOR_TARGET_INVALID")
        port = self._bound(handler, write=method == "POST")
        if path.path == INIT_PATH and method == "GET":
            handler.send_response(200)
            handler.send_header("Content-Type", "text/html; charset=utf-8")
            handler.send_header("Content-Length", str(len(INIT_HTML)))
            handler.send_header("Cache-Control", "no-store")
            handler.send_header("Referrer-Policy", "no-referrer")
            csp_hash = base64.b64encode(hashlib.sha256(_INIT_SCRIPT.encode()).digest()).decode()
            handler.send_header("Content-Security-Policy", f"default-src 'none'; script-src 'sha256-{csp_hash}'; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
            handler.end_headers()
            handler.wfile.write(INIT_HTML)
            return
        if path.path == PREFIX + "bootstrap" and method == "POST":
            body = self._body(handler)
            if set(body) != {"handoff"}:
                _fail("LOCAL_OPERATOR_BODY_INVALID")
            grant, review_path = consume_private_browser_handoff(self.store.sessions, body["handoff"], port=port)
            op_name, csrf_name = self.names(port)
            payload = json.dumps({"session_ready": True, "execution_authority": False,
                                  "review_path": review_path, "csrf_cookie_name": csrf_name}).encode()
            handler.send_response(200)
            handler.send_header("Content-Type", "application/json; charset=utf-8")
            handler.send_header("Content-Length", str(len(payload)))
            handler.send_header("Cache-Control", "no-store")
            handler.send_header("Set-Cookie", f"{op_name}={grant.session_id}.{grant.capability}; HttpOnly; SameSite=Strict; Path=/; Max-Age=3600")
            handler.send_header("Set-Cookie", f"{csrf_name}={grant.csrf}; SameSite=Strict; Path=/; Max-Age=3600")
            handler.end_headers()
            handler.wfile.write(payload)
            return
        if path.path == PREFIX + "context" and method == "GET":
            # Nonsecret routing metadata is not an authentication result.
            launcher = Path(__file__).resolve().parents[1] / 'scripts' / 'private_local_browser_bootstrap.py'
            launcher_args = [sys.executable, str(launcher), '--private-root',
                             str(self.store.sessions.authority.root), '--port', str(port)]
            # This is cmd.exe guidance, never executed by the service. Quote
            # every argument; do not offer a command if shell expansion could
            # change an unusual path, even inside quotes.
            launcher_prefix = '' if any(any(ch in arg for ch in '\"%!\r\n') for arg in launcher_args) else ' '.join('"' + arg + '"' for arg in launcher_args)
            handler._json(200, {"channel_installed": True, "session_ready": False,
                                "csrf_cookie_name": self.names(port)[1], "execution_authority": False,
                                "recovery_launcher_prefix": launcher_prefix,
                                "recovery_requires_same_windows_user": True})
            return
        grant = self._grant(handler, port)
        if path.path == PREFIX + "status" and method == "GET":
            handler._json(200, {"session_ready": True, "execution_authority": False,
                                "csrf_cookie_name": self.names(port)[1]})
            return
        if path.path == PREFIX + "prepare" and method == "POST":
            body = self._body(handler)
            if set(body) not in ({"reservation_id"}, {"reservation_id", "marketplace_plan_id"}):
                _fail("LOCAL_OPERATOR_BODY_INVALID")
            handler._json(200, self.store.prepare(grant, reservation_id=body["reservation_id"],
                                                  marketplace_plan_id=body.get('marketplace_plan_id')))
            return
        if path.path == PREFIX + "decision" and method == "POST":
            body = self._body(handler)
            if set(body) != {"nonce", "review_digest"}:
                _fail("LOCAL_OPERATOR_BODY_INVALID")
            decision = self.store.decide(grant, nonce=body["nonce"], review_digest=body["review_digest"])
            if self.fake_successor is not None:
                decision = execute_private_final_successor(self.store, grant, review_digest=body['review_digest'], transport=self.fake_successor)
            handler._json(200, decision)
            return
        if path.path == PREFIX + 'resume' and method == 'POST':
            body = self._body(handler)
            if set(body) != {'review_digest'} or self.fake_successor is None:
                _fail('PRIVATE_FINAL_FAKE_REQUIRED')
            handler._json(200, execute_private_final_successor(self.store, grant, review_digest=body['review_digest'], transport=self.fake_successor))
            return
        _fail("LOCAL_OPERATOR_ACTION_INVALID")


def handle_private_local_review(handler, *, method):
    path = urlsplit(handler.path).path
    if path != INIT_PATH and not path.startswith(PREFIX):
        return False
    installed = getattr(handler.server, "private_local_review", None)
    if type(installed) is not PrivateLocalReviewHttp:
        handler._json(503, {"error": "LOCAL_OPERATOR_FEATURE_DISABLED", "execution_authority": False})
        return True
    try:
        installed.handle(handler, method=method)
    except CommonAuthorityBlocked as error:
        handler._json(403, {"error": str(error), "execution_authority": False})
    except (OSError, ValueError, TypeError, KeyError):
        handler._json(409, {"error": "LOCAL_OPERATOR_UNAVAILABLE", "execution_authority": False})
    return True
