"""Subprocess fixture: real HTTP identity, synthetic code/config, no business I/O."""
import json
import os
from pathlib import Path
import socket
import sqlite3
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared_platform.runtime_identity import capture_runtime_identity, health_payload
from core import config
from shared_platform import report_store, release_store, workbench_store

root = Path(sys.argv[1]).resolve()
profile = root / 'runtime-profile.json'
os.environ['ORBIT_RUNTIME_PROFILE'] = str(profile)
config.CONFIG_PATH = root / 'config/settings.json'
selected = root / 'selected-settings.txt'
if selected.is_file():
    config.CONFIG_PATH = Path(selected.read_text(encoding='utf-8'))
config.FALLBACK_CONFIG_PATHS = []
config.load_settings()  # Only this generated fixture, records real cache provenance.
report_store.DEFAULT_REPORT_STORE_PATH = root / 'data/orbit_platform.db'
release_store.DEFAULT_RELEASE_STORE_PATH = root / 'data/orbit_platform.db'
workbench_store.WorkbenchStore.__init__.__defaults__ = (root / 'data/orbit_workbench.db',)
startup = capture_runtime_identity('orbit-hive-local-console', root=root)

def forbidden(*args, **kwargs):
    raise AssertionError('database/network/config reload forbidden in process fixture')
sqlite3.connect = forbidden
socket.socket.connect = forbidden
config.load_settings = forbidden

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        # The file contains a non-secret test control path; never a business config.
        selected = root / 'selected-settings.txt'
        if selected.is_file():
            config.CONFIG_PATH = Path(selected.read_text(encoding='utf-8'))
        body = json.dumps(health_payload('orbit-hive-local-console', root=root, startup=startup)).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
    def log_message(self, *args): pass

server = HTTPServer(('127.0.0.1', 0), Handler)
print(json.dumps({'pid': os.getpid(), 'port': server.server_port, 'startup': startup}), flush=True)
server.serve_forever()
