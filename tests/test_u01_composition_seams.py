"""Combined source navigation and supply UI, using explicit synthetic data."""
import json
import os
from pathlib import Path
import subprocess
import threading
from http.server import ThreadingHTTPServer


def test_combined_handler_navigation_and_supply(tmp_path, record_property):
    from modules.products import server
    source = Path(__file__).resolve().parents[1]
    dashboard = source / 'domains/supply_chain_operations/dashboard'
    fixture = source / 'tests/fixtures/u01_combination'

    class FixtureHandler(server.Handler):
        def _file(self, path, *, cache_seconds=None):
            if Path(path) in {dashboard / 'data.js', dashboard / 'inbound-plan.js'}:
                path = fixture / Path(path).name
            return super()._file(path, cache_seconds=cache_seconds)

        def do_POST(self):
            self.send_error(403, 'Read-only composition fixture')

    httpd = ThreadingHTTPServer(('127.0.0.1', 0), FixtureHandler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    try:
        for script in ['u01_composition_seams.cjs', 'u04b_dense_browser.cjs']:
            out = tmp_path / Path(script).stem
            out.mkdir()
            result = subprocess.run([os.environ['ORBIT_NODE_BIN'], str(source / 'tests/browser' / script),
                f'http://127.0.0.1:{httpd.server_port}/', str(out)],
                capture_output=True, text=True, encoding='utf-8', timeout=120)
            evidence = json.loads((out / 'result.json').read_text(encoding='utf-8'))
            assert result.returncode == 0, (result.stdout + result.stderr, evidence)
            assert all(item['ok'] for item in evidence['checks'])
            record_property(script, str(out))
    finally:
        httpd.shutdown()
        worker.join(timeout=3)
        httpd.server_close()
