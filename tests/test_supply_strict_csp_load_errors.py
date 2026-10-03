"""Original strict-CSP Handler with synthetic, immutable supply assets only."""
import json
import os
from pathlib import Path
import subprocess

from test_audited_supply_snapshot import snapshot, snapshot_http

ROOT = Path(__file__).resolve().parents[1]


def test_original_legacy_handler_strict_csp_asset_failure_shows_terminal_banner_and_retry(snapshot, snapshot_http, tmp_path, monkeypatch):
    server, get = snapshot_http
    # The formal web-only deployment has no capture configuration. Keep its
    # legacy serving mode while substituting only owned synthetic data/assets.
    monkeypatch.setattr(server, 'supply_chain_capture', None)
    dashboard = snapshot[0] / 'domains/supply_chain_operations/dashboard'
    (dashboard / 'data.js').write_bytes(snapshot[-2]['data'])
    (dashboard / 'inbound-plan.js').write_bytes(snapshot[-2]['inbound'])
    (dashboard / 'assets').mkdir()
    (dashboard / 'assets/sku-0001.png').write_bytes(snapshot[-1])
    status, headers, _ = get('/supply-chain/')
    assert status == 200
    csp = headers['Content-Security-Policy']
    assert "script-src 'self'" in csp and "style-src 'self'" in csp
    assert 'unsafe-inline' not in csp and 'sha256-' not in csp
    node = Path(os.environ['ORBIT_NODE_BIN'])
    command = [str(node), str(ROOT / 'tests/browser/supply_strict_csp_load_errors.cjs'),
               f'http://127.0.0.1:{server.server_port}', str(tmp_path), csp]
    run = subprocess.run(command, capture_output=True, text=True, encoding='utf-8', timeout=45)
    (tmp_path / 'browser-command.json').write_text(
        json.dumps({'command': command, 'exit': run.returncode, 'stdout': run.stdout, 'stderr': run.stderr}), encoding='utf-8')
    proof = json.loads((tmp_path / 'strict-csp-browser-result.json').read_text(encoding='utf-8'))
    assert run.returncode == 0, json.dumps(proof, ensure_ascii=False) + '\n' + run.stderr
    assert proof['status'] == 'PASS'
    assert proof['headers'] == [csp, csp, csp]
    assert proof['blockedOwnedAssetCount'] == 1 and proof['visibleTerminalFailure'] is True
    assert proof['recoveredSameSyntheticRows'] is True
    assert proof['foreignRequests'] == [] and proof['businessPosts'] == []
    assert proof['healthyScriptViolations'] == []
