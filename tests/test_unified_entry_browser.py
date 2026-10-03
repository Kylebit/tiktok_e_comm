"""Real Chromium journey over guarded loopback application handlers."""
import importlib
import json
import os
from pathlib import Path
import subprocess
from threading import Thread
from http.server import ThreadingHTTPServer

from test_unified_entry import ROOT, entry_http  # shared no-DB/config-init fixture


def test_unified_entry_browser_contract(entry_http, tmp_path, monkeypatch):
    _, main, profile = entry_http
    from scripts import product_publication_runtime as runtime
    servers, threads = [main], []
    for module_name, handler_name in (
        ('modules.sourcing.new_product_server', 'NewProductHandler'),
        ('modules.ozon.rus_server', 'OrbitRusHandler'),
    ):
        module = importlib.import_module(module_name)
        from shared_platform.runtime_identity import capture_runtime_identity
        monkeypatch.setattr(module, 'RUNTIME_IDENTITY', capture_runtime_identity('new_product' if module_name.endswith('new_product_server') else 'orbit_rus', root=ROOT))
        server = ThreadingHTTPServer(('127.0.0.1', 0), getattr(module, handler_name))
        main.fixture_allowed_ports.add(server.server_port)
        thread = Thread(target=server.serve_forever, kwargs={'poll_interval': .01}, daemon=True)
        thread.start(); servers.append(server); threads.append(thread)
    original_specs = runtime.service_specs
    def specs(**kwargs):
        selected = original_specs(root=ROOT, include_rus=kwargs.get('include_rus', False), profile_path=profile)
        return tuple(row._replace(port=servers[i].server_port, health_url=f'http://127.0.0.1:{servers[i].server_port}' + ('/api/health' if i == 0 else '/health')) for i, row in enumerate(selected))
    monkeypatch.setattr(runtime, 'service_specs', specs)
    preview = {'url': f'http://127.0.0.1:{main.server_port}/', 'root': str(ROOT),
               'profile': str(profile), 'services': [{'port': server.server_port} for server in servers]}
    (tmp_path / 'preview.json').write_text(json.dumps(preview), encoding='utf-8')
    bundle = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node = os.environ.get('ORBIT_NODE_BIN') or str(bundle / 'bin/node.exe')
    packages = os.environ.get('ORBIT_NODE_MODULES') or str(bundle / 'node_modules')
    try:
        result = subprocess.run([node, str(ROOT / 'tests/unified_entry_browser.cjs'), str(tmp_path)],
                                env={**os.environ, 'ORBIT_NODE_MODULES': packages},
                                capture_output=True, text=True, encoding='utf-8', timeout=60,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        assert result.returncode == 0, result.stdout + result.stderr
        record = json.loads((tmp_path / 'browser-result.json').read_text(encoding='utf-8'))
        assert record['status'] == 'passed'
        assert record['external'] == []
    finally:
        for server in servers[1:]: server.shutdown(); server.server_close()
        for thread in threads: thread.join(timeout=5)
