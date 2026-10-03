"""Knowledge directory journey over the existing isolated application handler."""
import json
import os
from pathlib import Path
import subprocess

from test_unified_entry import ROOT, entry_http


def test_knowledge_directory_browser(entry_http, tmp_path):
    _, server, _ = entry_http
    (tmp_path / 'preview.json').write_text(json.dumps({
        'url': f'http://127.0.0.1:{server.server_port}/', 'root': str(ROOT),
        'fixture': True,
    }), encoding='utf-8')
    bundle = Path.home() / '.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    result = subprocess.run([
        os.environ.get('ORBIT_NODE_BIN', str(bundle / 'bin/node.exe')),
        str(ROOT / 'tests/knowledge_directory_browser.cjs'), str(tmp_path),
    ], env={**os.environ, 'ORBIT_NODE_MODULES': os.environ.get('ORBIT_NODE_MODULES', str(bundle / 'node_modules'))},
        capture_output=True, text=True, encoding='utf-8', timeout=90,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    assert result.returncode == 0, result.stdout + result.stderr
    record = json.loads((tmp_path / 'knowledge-browser-result.json').read_text(encoding='utf-8'))
    assert record['status'] == 'passed'
    assert record['external'] == []
    assert record['errors'] == []
