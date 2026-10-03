"""Actual task page, closed public DTOs; no task or provider write."""
import json
import os
from pathlib import Path
import subprocess

from test_release_ux_contract import _browser_runtime


def test_actual_task_page_distinguishes_scoped_preparation_registration_and_readonly_observation(tmp_path):
    runtime = _browser_runtime()
    assert runtime, 'Fixed real browser runtime required; no skip'
    node, modules = runtime
    assert os.environ.get('ORBIT_BROWSER_EXECUTABLE'), 'Fixed browser executable required'
    result = subprocess.run([str(node), str(Path(__file__).parent / 'browser/task_workspace_scoped_status.cjs'),
        str(tmp_path)], env=dict(os.environ, NODE_PATH=str(modules)),
        capture_output=True, text=True, encoding='utf-8', timeout=100)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout)
    assert proof['synthetic'] is True and proof['widths'] == [1440, 390]
    assert proof['posts'] == [] and proof['errors'] == []
    assert len(proof['journeys']) == 9
