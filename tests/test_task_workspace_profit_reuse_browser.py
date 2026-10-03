"""Original real-browser harness, with intercepted returned-task creation DTOs."""
import json
import os
from pathlib import Path
import subprocess

from test_release_ux_contract import _browser_runtime


def test_profit_create_opens_exact_reused_task_month_status_and_original_result(tmp_path):
    runtime = _browser_runtime()
    assert runtime, 'Fixed real browser runtime required; no skip'
    node, modules = runtime
    assert os.environ.get('ORBIT_BROWSER_EXECUTABLE'), 'Fixed browser executable required'
    result = subprocess.run([str(node), str(Path(__file__).parent / 'browser/task_workspace_profit_reuse.cjs'),
        str(tmp_path)], env=dict(os.environ, NODE_PATH=str(modules)),
        capture_output=True, text=True, encoding='utf-8', timeout=100)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout)
    assert proof['synthetic'] is True and proof['widths'] == [1440, 390]
    assert proof['cases'] == ['old-completed', 'old-unknown', 'old-offline', 'same-source', 'missing-source']
    assert proof['closed_task_creates'] == 10 and proof['other_posts'] == []
    assert proof['errors'] == [] and proof['provider_calls'] == 0
