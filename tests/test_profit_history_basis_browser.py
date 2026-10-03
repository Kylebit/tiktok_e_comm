"""The separate partial review disclosure must not change historical money or rows."""
import json
import os
from pathlib import Path
import subprocess
from http.server import ThreadingHTTPServer
from threading import Thread

from modules.products import server
from test_release_ux_contract import _browser_runtime
from domains.data_operations.profit_settlement.historical_partial_view import load_historical_partial_candidate


def test_history_reference_is_read_only_and_keeps_partial_facts(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setattr(server, 'ROOT', root)
    monkeypatch.setattr(server, 'WEB_DIR', root / 'web')
    before = load_historical_partial_candidate(root)
    class Handler(server.Handler):
        def do_POST(self):
            raise AssertionError('Historical reference has no write operation')
        def log_message(self, *args):
            pass
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        available = _browser_runtime()
        assert available, 'Real browser runtime required'
        node, modules = available
        run = subprocess.run([str(node), str(root/'tests/browser/profit_history_basis.cjs'),
            f'http://127.0.0.1:{httpd.server_port}', str(tmp_path)],
            env={**os.environ, 'NODE_PATH': str(modules)}, capture_output=True,
            text=True, encoding='utf-8', timeout=60)
        assert run.returncode == 0, run.stdout + run.stderr
        assert json.loads(run.stdout)['requests_are_read_only'] is True
        assert load_historical_partial_candidate(root) == before
        assert all(row['historical_cost_cny'] is None and row['fx_cny_per_local'] is None
            and row['profit_cny'] is None for row in before['rows'])
    finally:
        httpd.shutdown(); httpd.server_close(); thread.join(5)
