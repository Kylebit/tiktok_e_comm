from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import os
from pathlib import Path
import subprocess
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]

def test_editable_final_decision_sheet(tmp_path):
    dashboard = ROOT / 'domains/supply_chain_operations/dashboard'
    node_root = Path('C:/Users/Windows11/.cache/codex-runtimes/codex-primary-runtime/dependencies/node')
    server = ThreadingHTTPServer(('127.0.0.1', 0), partial(SimpleHTTPRequestHandler, directory=str(dashboard)))
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        result = subprocess.run([str(node_root/'bin/node.exe'), str(ROOT/'tests/browser/supply_decision_sheet.cjs'),
            f'http://127.0.0.1:{server.server_port}', str(dashboard), str(tmp_path)],
            env={**os.environ,'NODE_PATH':str(node_root/'node_modules')}, capture_output=True,
            text=True, encoding='utf-8', timeout=90)
        assert result.returncode == 0, result.stdout + result.stderr
    finally:
        server.shutdown(); server.server_close(); worker.join(timeout=5)
