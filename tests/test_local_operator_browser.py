"""Actual init script/browser cookies + original page redirect, private data only."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
from threading import Thread
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit

from modules.products import server
from test_private_local_final_review import _ready
from test_release_ux_contract import _browser_runtime
from shared_platform.local_operator_http import PrivateLocalReviewHttp, handle_private_local_review
from shared_platform.local_operator_session import initialize_private_browser_handoff, issue_private_browser_handoff


def test_actual_browser_fragment_cookies_replay_and_original_target(tmp_path, monkeypatch):
    store, _, _ = _ready(tmp_path)
    initialize_private_browser_handoff(store.sessions)
    monkeypatch.setattr(server, 'WEB_DIR', Path(__file__).resolve().parents[1] / 'web')
    class Handler(server.Handler):
        def do_GET(self):
            if handle_private_local_review(self, method='GET'):
                return
            if urlsplit(self.path).path.startswith('/api/'):
                return self._json(503, {'ok':False,'error':'Private bootstrap fixture: no business provider'})
            return super().do_GET()
        def do_POST(self):
            if handle_private_local_review(self, method='POST'):
                return
            return self._json(503, {'ok':False,'error':'Private fixture: provider disabled'})
        def log_message(self, *_):
            pass
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    httpd.private_local_review = PrivateLocalReviewHttp(store)
    thread = Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        nonce = issue_private_browser_handoff(store.sessions, port=httpd.server_port, reservation_id='private-reservation')
        handoff = store.authority.root / 'browser-carrier.txt'
        handoff.write_text(nonce)
        runtime = _browser_runtime()
        assert runtime, 'Explicit Node/Playwright runtime required'
        node, modules = runtime
        env = dict(os.environ, NODE_PATH=str(modules))
        assert env.get('ORBIT_BROWSER_EXECUTABLE'), 'Explicit Chromium executable required'
        result = subprocess.run([str(node), str(Path(__file__).parent / 'browser/local_operator_bootstrap.cjs'),
            f'http://127.0.0.1:{httpd.server_port}', str(handoff), str(tmp_path)],
            env=env, capture_output=True, text=True, encoding='utf-8', timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
        facts = json.loads(result.stdout)
        assert facts['fragment_cleared_before_post'] and facts['replay_same_decision']
        with sqlite3.connect(store.authority.store.path) as db:
            assert db.execute('SELECT COUNT(*) FROM private_final_decisions').fetchone()[0] == 1
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(5)
