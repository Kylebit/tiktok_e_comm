"""Bounded recovery regressions. Private data, native owner, no real provider."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from threading import Thread
from types import SimpleNamespace
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit

import pytest

from modules.products import server
from shared_platform.common_offer_authority_store import CommonAuthorityBlocked
from shared_platform.local_operator_session import (
    initialize_private_browser_handoff, issue_private_browser_handoff,
    consume_private_browser_handoff,
)
from shared_platform.local_operator_http import PrivateLocalReviewHttp, handle_private_local_review
from shared_platform.private_final_decision_store import PrivateFinalFakeTransport
from shared_platform.private_domain_final_review import project_registered_domain_stage
from test_private_local_final_review import _ready, _approved, _counts
from test_local_operator_original_review import _complete_domain_fixture
from test_release_ux_contract import _browser_runtime


def _expire(monkeypatch):
    # Session records are immutable. Move the test clock through the actual
    # TTL check instead of dropping protection or rewriting a grant.
    import shared_platform.local_operator_session as native
    now = native.time.time()
    # Python modules share the same time object. Replace only this module's
    # clock reference, so expiring identity does not also expire the fixture's
    # separate COMMON policy and change the case into a policy-revocation test.
    monkeypatch.setattr(native, 'time', SimpleNamespace(time=lambda: now + 3601))


def test_matching_expired_identity_is_technical_not_new_review(tmp_path, monkeypatch):
    store, grant, _ = _ready(tmp_path)
    _expire(monkeypatch)
    with pytest.raises(CommonAuthorityBlocked, match='^LOCAL_OPERATOR_SESSION_EXPIRED$'):
        store.prepare(grant, reservation_id='private-reservation')
    assert set(_counts(store).values()) == {0}


def test_forged_expired_capability_does_not_get_authenticated_expiry_reason(tmp_path, monkeypatch):
    store, grant, _ = _ready(tmp_path)
    _expire(monkeypatch)
    with pytest.raises(CommonAuthorityBlocked, match='^LOCAL_CAPABILITY_INVALID_OR_EXPIRED$'):
        store.prepare(replace(grant, capability='0' * 64), reservation_id='private-reservation')
    assert set(_counts(store).values()) == {0}


def test_same_owner_native_handoff_reconnects_without_human_decision(tmp_path, monkeypatch):
    store, grant, _ = _ready(tmp_path)
    _expire(monkeypatch)
    initialize_private_browser_handoff(store.sessions)
    nonce = issue_private_browser_handoff(store.sessions, port=43210, reservation_id='private-reservation')
    new_grant, path = consume_private_browser_handoff(store.sessions, nonce, port=43210)
    assert new_grant.session_id != grant.session_id and path.startswith('/product-workspace?offer_id=')
    assert store.prepare(new_grant, reservation_id='private-reservation')['review_digest']
    assert set(_counts(store).values()) == {0}


def test_saved_decision_reused_by_fresh_same_owner_session(tmp_path, monkeypatch):
    store, grant, _, prepared, approval = _approved(tmp_path)
    _expire(monkeypatch)
    new_grant = store.sessions.grant_same_user()
    new_prepared = store.prepare(new_grant, reservation_id='private-reservation')
    assert new_prepared['review_digest'] == prepared['review_digest']
    assert store.decide(new_grant, nonce=new_prepared['nonce'], review_digest=new_prepared['review_digest']) == approval
    assert set(_counts(store).values()) == {1}


def test_changed_complete_display_not_recovered_as_same_review(tmp_path, monkeypatch):
    store, _documents, _dashboard, data, market, _common_fake, _reservation = _complete_domain_fixture(tmp_path, monkeypatch)
    changed = deepcopy(market)
    changed['preview']['review_manifest']['copy_sets'][0]['title'] += ' changed after reconnect'
    with pytest.raises(CommonAuthorityBlocked, match='DOMAIN_DISPLAYED_BYTES_CHANGED'):
        project_registered_domain_stage(store, {'ok': True, 'offer_id': data['offer_id'],
            'common': {'status': 'VERIFIED'}, 'marketplace': changed})
    assert set(_counts(store).values()) == {0}


def test_original_page_expiry_native_reconnect_and_readonly_refresh(tmp_path, monkeypatch):
    store, documents, dashboard, data, market, common_fake, reservation_id = _complete_domain_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(server, 'WEB_DIR', Path(__file__).resolve().parents[1] / 'web')
    frozen_dashboard = deepcopy(dashboard)
    frozen_dashboard.update(frozen_first_review=deepcopy(documents['first_review']), frozen_review_projection=True)
    frozen_dashboard['product']['actual_product_approved'] = True
    fake, requests = PrivateFinalFakeTransport(), []
    clock = tmp_path / 'fixture-clock-offset.txt'
    clock.write_text('0', encoding='utf-8')
    import shared_platform.local_operator_session as native
    real_time = native.time.time
    monkeypatch.setattr(native.time, 'time', lambda: real_time() + int(clock.read_text(encoding='utf-8')))

    class Handler(server.Handler):
        def do_GET(self):
            if handle_private_local_review(self, method='GET'):
                return
            path = urlsplit(self.path).path
            if path == '/api/product-workspace/dashboard':
                return self._json(200, server._product_workspace_view(deepcopy(frozen_dashboard)))
            if path == '/api/product-workspace/publication-stages':
                return super().do_GET()
            if path == '/api/product-flow/preview':
                return self._json(200, {'ok': True, 'offer_id': data['offer_id'],
                    'source': {'images': []}, 'review': {'image_actions': []}, 'read_only': True})
            if path.startswith('/api/'):
                return self._json(503, {'ok': False, 'error': 'Private fixture has no business provider'})
            return super().do_GET()

        def do_POST(self):
            requests.append(urlsplit(self.path).path)
            if handle_private_local_review(self, method='POST'):
                return
            raise AssertionError('Legacy approval or provider call forbidden')

        def log_message(self, *_):
            pass

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    httpd.private_local_review = PrivateLocalReviewHttp(store, fake_successor=fake)
    thread = Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        nonce = issue_private_browser_handoff(store.sessions, port=httpd.server_port,
            reservation_id=reservation_id, marketplace_plan_id=data['plan_id'])
        carrier = store.authority.root / 'recovery-carrier.txt'
        carrier.write_text(nonce, encoding='utf-8')
        # This controller is a native owner program invoked by Node, NOT an
        # HTTP endpoint that issues an identity. It advances the private test
        # clock, then calls the actual existing native handoff producer.
        controller = tmp_path / 'native_recovery_controller.py'
        source_root = str(Path(__file__).resolve().parents[1])
        controller.write_text(
            'import sys,time\n'
            f'sys.path.insert(0,{source_root!r})\n'
            'from pathlib import Path\n'
            'from shared_platform.common_offer_authority_store import PrivateCommonAuthorityStore\n'
            'from shared_platform.private_final_decision_store import PrivateFinalDecisionStore\n'
            'from shared_platform.local_operator_session import issue_private_browser_handoff\n'
            'root,carrier,port,reservation,plan,clock_file,mode=sys.argv[1:]\n'
            'p=Path(carrier)\n'
            'clock=Path(clock_file)\n'
            "if mode=='expire':\n"
            " clock.write_text(str(int(clock.read_text(encoding='utf-8'))+3601),encoding='utf-8')\n"
            'else:\n'
            " offset=int(clock.read_text(encoding='utf-8')); real_time=time.time\n"
            ' time.time=lambda:real_time()+offset\n'
            ' store=PrivateFinalDecisionStore(PrivateCommonAuthorityStore.open(root))\n'
            ' nonce=issue_private_browser_handoff(store.sessions,port=int(port),reservation_id=reservation,marketplace_plan_id=plan)\n'
            " p.write_text(nonce,encoding='utf-8')\n", encoding='utf-8')
        def media_urls(value):
            if isinstance(value, dict):
                return set().union(*(media_urls(part) for part in value.values()))
            if isinstance(value, list):
                return set().union(*(media_urls(part) for part in value))
            return {value} if isinstance(value, str) and value.startswith('https://') else set()
        expected = tmp_path / 'recovery-expected.json'
        expected.write_text(json.dumps({'manifest': market['preview']['review_manifest'],
            'source_media_urls': sorted(media_urls(documents) | media_urls(dashboard)),
            'private_root': str(store.authority.root), 'port': httpd.server_port,
            'reservation_id': reservation_id, 'plan_id': data['plan_id'], 'clock_file': str(clock)}), encoding='utf-8')
        runtime = _browser_runtime()
        assert runtime, 'Explicit browser runtime required'
        node, modules = runtime
        env = dict(os.environ, NODE_PATH=str(modules))
        assert env.get('ORBIT_BROWSER_EXECUTABLE'), 'Explicit Chromium executable required'
        import sys
        result = subprocess.run([str(node), str(Path(__file__).parent / 'browser/local_operator_session_recovery.cjs'),
            f'http://127.0.0.1:{httpd.server_port}', str(carrier), str(expected), str(tmp_path),
            sys.executable, str(controller)], env=env, capture_output=True, text=True, encoding='utf-8', timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        facts = json.loads(result.stdout)
        assert facts['readonly_refresh_posts'] == 0 and facts['decision_requests'] == 1
        assert facts['full_original_dom'] and facts['same_owner_reconnected']
        assert common_fake.mutations == 0 and len(fake.writes) == 1
        assert all(path.startswith('/api/product-workspace/local-operator/') for path in requests)
        assert set(_counts(store).values()) == {1}
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(5)
        assert not thread.is_alive()
