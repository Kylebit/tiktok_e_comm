"""The knowledge page distinguishes Skill source and installation states."""

import json
import os
from pathlib import Path
import subprocess

import pytest
from test_unified_entry import entry_http


ROOT = Path(__file__).resolve().parents[1]


def test_skill_identity_statuses_in_real_browser(entry_http, tmp_path):
    _, server, _ = entry_http
    node = Path(os.environ.get('ORBIT_NODE_BIN', Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node.exe'))
    modules = Path(os.environ.get('ORBIT_NODE_MODULES', node.parent.parent/'node_modules'))
    if not node.is_file() or not modules.is_dir():
        if os.environ.get('ORBIT_REQUIRE_BROWSER_TESTS') == '1':
            pytest.fail('required browser runtime missing')
        pytest.skip('browser runtime unavailable')
    result = subprocess.run([str(node), str(ROOT/'tests/browser/skill_identity_cards.cjs'),
                             f'http://127.0.0.1:{server.server_port}', str(tmp_path)],
                            cwd=ROOT, env={**os.environ,'ORBIT_NODE_MODULES':str(modules)},
                            capture_output=True,text=True,encoding='utf-8',timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads((tmp_path/'skill-identity-browser.json').read_text(encoding='utf-8'))['status'] == 'passed'
