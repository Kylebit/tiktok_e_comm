"""Actual original full-review DOM + domain producer, isolated synthetic I/O.

The typed reader must be installed before this test runs. No caller-authored
FrozenReview, replacement compiler status, tiny reservation or real provider.
"""
from copy import deepcopy
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import pytest
from threading import Thread
from urllib.parse import urlsplit

from modules.products import server
from shared_platform import release_store
from shared_platform.local_operator_http import PrivateLocalReviewHttp, handle_private_local_review
from shared_platform.local_operator_session import initialize_private_browser_handoff, issue_private_browser_handoff
from shared_platform.private_domain_final_review import initialize_domain_bindings, register_domain_candidate, project_registered_domain_stage
from shared_platform.private_final_decision_store import PrivateFinalDecisionStore, PrivateFinalFakeTransport
from test_common_offer_authority_store import _authority
from test_b4b_publication_preview import reviewed_marketplace
from test_release_ux_contract import _browser_runtime
from shared_platform.common_offer_authority_store import CommonAuthorityBlocked


def _complete_domain_fixture(tmp_path, monkeypatch):
    # Only the fixture's ReleaseStore constructor is redirected to the owned
    # release.db. Actual compiler/producer/reader/decision methods stay real.
    authority = _authority(tmp_path, imported=False)
    with monkeypatch.context() as factory_patch:
        factory_patch.setattr(release_store, 'ReleaseStore', lambda *_args, **_kwargs: authority.store)
        documents, dashboard, domain, data, common_fake, market = reviewed_marketplace(tmp_path, monkeypatch)
    assert domain is authority.store
    assert common_fake.mutations == 0
    assert market['final_review_available'] is False
    assert market['final_review_admission']['execution_authority'] is False
    assert market['preview']['status'] == 'READY_FOR_FINAL_REVIEW'
    domain.create_plan(market['plan']['payload'])
    # Native importer is allowed only after real SID/owner-only ACL bootstrap.
    store = PrivateFinalDecisionStore(authority)
    store.migrate()
    store.sessions.initialize_owner()
    from shared_platform.r3_domain_common_proof import PrivateDomainCommonProofReader
    reader = PrivateDomainCommonProofReader(authority)
    reader.initialize_fixture_schema()
    fixture_receipt = reader.register_fixture(data['plan_id'])
    assert fixture_receipt['evidence_kind'] == 'SYNTHETIC_TEST_ONLY'
    assert fixture_receipt['execution_authority'] is False
    reservation_id = fixture_receipt['common_reservation_id']
    initialize_private_browser_handoff(store.sessions)
    initialize_domain_bindings(store)
    descriptor = register_domain_candidate(store, common_reservation_id=reservation_id,
                                            marketplace_plan_id=data['plan_id'])
    assert descriptor['candidate_digest'] == market['preview']['candidate_digest']
    assert descriptor['evidence_kind'] == 'SYNTHETIC_TEST_ONLY'
    return store, documents, dashboard, data, market, common_fake, reservation_id


def test_display_bytes_change_cannot_reuse_same_candidate_digest(tmp_path, monkeypatch):
    store, _documents, _dashboard, data, market, _common_fake, _reservation_id = _complete_domain_fixture(tmp_path, monkeypatch)
    displayed = deepcopy(market)
    # A caller/cache could leave the old digest while changing visible copy.
    # The service must bind the complete actual display, not just that label.
    displayed['preview']['review_manifest']['copy_sets'][0]['title'] = 'Changed visible title with unchanged candidate digest'
    with pytest.raises(CommonAuthorityBlocked, match='DOMAIN_DISPLAYED_BYTES_CHANGED'):
        project_registered_domain_stage(store, {'ok': True, 'offer_id': data['offer_id'],
            'common': {'status': 'VERIFIED'}, 'marketplace': displayed})
    with sqlite3.connect(store.authority.store.path) as db:
        assert db.execute('SELECT COUNT(*) FROM private_final_decisions').fetchone()[0] == 0


@pytest.mark.parametrize('response_mode', ['normal', 'drop-response'])
def test_original_complete_candidate_one_click_and_stale_tab_replay(tmp_path, monkeypatch, response_mode):
    store, documents, dashboard, data, market, common_fake, reservation_id = _complete_domain_fixture(tmp_path, monkeypatch)
    monkeypatch.setattr(server, 'WEB_DIR', Path(__file__).resolve().parents[1] / 'web')
    frozen_dashboard = deepcopy(dashboard)
    frozen_dashboard.update(frozen_first_review=deepcopy(documents['first_review']),
                            frozen_review_projection=True)
    frozen_dashboard['product']['actual_product_approved'] = True
    fake = PrivateFinalFakeTransport()
    requests = []

    class Handler(server.Handler):
        def do_GET(self):
            if handle_private_local_review(self, method='GET'):
                return
            path = urlsplit(self.path).path
            if path == '/api/product-workspace/dashboard':
                return self._json(200, server._product_workspace_view(deepcopy(frozen_dashboard)))
            if path == '/api/product-workspace/publication-stages':
                # Exercise the real handler, domain restore, redaction and
                # registered projection rather than returning a fabricated
                # stage response. default_release_store is this owned fixture.
                return super().do_GET()
            if path == '/api/product-flow/preview':
                return self._json(200, {'ok': True, 'offer_id': data['offer_id'],
                    'source': {'images': []}, 'review': {'image_actions': []}, 'read_only': True})
            if path.startswith('/api/'):
                return self._json(503, {'ok': False, 'error': 'Synthetic fixture has no business provider'})
            return super().do_GET()

        def do_POST(self):
            requests.append(urlsplit(self.path).path)
            if handle_private_local_review(self, method='POST'):
                return
            raise AssertionError('Legacy approval or real provider endpoint is forbidden')

        def log_message(self, *_args):
            pass

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    httpd.private_local_review = PrivateLocalReviewHttp(store, fake_successor=fake)
    thread = Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        nonce = issue_private_browser_handoff(store.sessions, port=httpd.server_port,
                    reservation_id=reservation_id, marketplace_plan_id=data['plan_id'])
        handoff = store.authority.root / 'original-browser-carrier.txt'
        handoff.write_text(nonce, encoding='utf-8')
        expected = tmp_path / 'actual-compiler-review.json'
        def media_urls(value):
            if isinstance(value, dict):
                return set().union(*(media_urls(part) for part in value.values()))
            if isinstance(value, list):
                return set().union(*(media_urls(part) for part in value))
            return {value} if isinstance(value, str) and value.startswith('https://') else set()
        expected.write_text(json.dumps({'manifest': market['preview']['review_manifest'],
            'source_media_urls': sorted(media_urls(documents) | media_urls(dashboard)),
            'compiler_status': market['preview']['status']}), encoding='utf-8')
        runtime = _browser_runtime()
        assert runtime, 'Explicit Node/Playwright runtime required'
        node, modules = runtime
        env = dict(os.environ, NODE_PATH=str(modules))
        assert env.get('ORBIT_BROWSER_EXECUTABLE'), 'Explicit Chromium executable required'
        result = subprocess.run([str(node), str(Path(__file__).parent / 'browser/local_operator_original_review.cjs'),
            f'http://127.0.0.1:{httpd.server_port}', str(handoff), str(expected), str(tmp_path), response_mode],
            env=env, capture_output=True, text=True, encoding='utf-8', timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        facts = json.loads(result.stdout)
        assert facts['full_original_dom'] and facts['same_candidate_replayed']
        assert facts['all_ordered_targets'] == ['tiktok:LH_PH', 'ozon:RU']
        assert facts['execution_authority'] is False
        assert facts['response_lost_after_persistence'] == (response_mode == 'drop-response')
        assert common_fake.mutations == 0 and len(fake.writes) == 1
        assert all(path.startswith('/api/product-workspace/local-operator/') for path in requests)
        with sqlite3.connect(store.authority.store.path) as db:
            for table in ('private_final_decisions', 'private_domain_final_approvals', 'private_final_successors'):
                assert db.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0] == 1
            assert db.execute('SELECT state FROM private_final_successors').fetchone()[0] == 'COMPLETE'
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(5)
        assert not thread.is_alive()
