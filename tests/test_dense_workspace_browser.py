"""Reproducible real-handler, generated-data Chrome acceptance for dense UI."""
from pathlib import Path
import json
import os
import subprocess
import sys
import time
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('stage_mode', ['unavailable', 'unavailable-identified', 'malformed-200'])
def test_dense_workspace_in_real_chrome(tmp_path, record_property, stage_mode):
    runtime = Path.home()/'.cache/codex-runtimes/codex-primary-runtime/dependencies/node'
    node = Path(os.environ.get('ORBIT_NODE_BIN', str(runtime/'bin/node.exe')))
    assert node.is_file(), 'Explicit Node runtime required; do not silently skip browser coverage'
    output = tmp_path/'browser'
    with (tmp_path/'fixture.log').open('w',encoding='utf-8') as log:
        process = subprocess.Popen([sys.executable,'-B',str(ROOT/'tests/dense_workspace_preview.py'),
            '--out',str(output),'--stage-mode',stage_mode],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT,
            env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'})
        try:
            deadline = time.monotonic()+20
            while not (output/'preview.json').exists():
                assert process.poll() is None, (tmp_path/'fixture.log').read_text(encoding='utf-8')
                assert time.monotonic()<deadline, 'Generated HTTP fixture failed to start'
                time.sleep(.05)
            preview=json.loads((output/'preview.json').read_text(encoding='utf-8'))
            assert Path(preview['root']).resolve()==ROOT.resolve()
            completed=subprocess.run([str(node),str(ROOT/'tests/dense_workspace_browser.cjs'),
                preview['url'],str(output),stage_mode],cwd=ROOT,capture_output=True,text=True,encoding='utf-8',timeout=150)
            assert completed.returncode==0, completed.stdout+completed.stderr
            result=json.loads((output/'browser-result.json').read_text(encoding='utf-8'))
            assert all(row['ok'] for row in result['results'])
            assert not result['blocked'] and not result['errors']
            assert not (output/'blocked-calls.json').exists()
            assert preview['business_database_connections']==0
            record_property('browser_checks',len(result['results']))
            record_property('renderer','installed Chrome; separate from native WebView')
            record_property('evidence',str(output))
        finally:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait(timeout=5)
