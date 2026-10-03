"""Real FLOW rendering of same-store getter facts; synthetic historical I/O only.

The existing native preparation chain and retained native-packet validator run
unchanged. Test-only approvals populate historical FK rows; they are never
standing-policy/account authority, and no provider mutation is performed.
"""
from copy import deepcopy
from pathlib import Path
import json
import os
import subprocess
from urllib.parse import urlsplit

import pytest

from modules.products import server, release_adapters
from shared_platform import operations_publication_common as common
from shared_platform import publication_r3_image_bridge as bridge
from shared_platform import release_control
from test_common_native_preparation_source import _native_common_chain
from test_native_common_write_census import (
    _historical_target, _normal_historical_evidence, _closed_comparison,
)
from test_r3_common_source_facts import connect, rows
from test_round1_workspace_freeze import live
from test_release_ux_contract import _browser_runtime


def _historical_write_and_reuse(store, payload, monkeypatch):
    historical = deepcopy(payload)
    historical['content_package_id'] += ':flow-historical-write'
    historical['plan_id'] = bridge.common_stage_plan_id(historical, offer_id=payload['product_id'])
    old = store.create_plan(historical)
    old_run = _historical_target(store, old)
    store.record_target_success(old_run['run_id'], 'miaoshou:COMMON',
        external_id=payload['product_id'],
        readback_evidence=_normal_historical_evidence(historical, monkeypatch))
    old_target = store.get_run(old_run['run_id'])['targets'][0]

    reused = deepcopy(payload)
    reused['content_package_id'] += ':flow-historical-reuse'
    reused['plan_id'] = bridge.common_stage_plan_id(reused, offer_id=payload['product_id'])
    reuse_plan = store.create_plan(reused, supersedes_plan_id=old['plan_id'])
    reuse_run = _historical_target(store, reuse_plan)
    source = _closed_comparison(reused, monkeypatch)
    comparison = {key: value for key, value in source.items()
                  if key not in {'native_common_observation', 'stored_common_lineage'}}
    comparison['predecessor'] = {
        'plan_id': old['plan_id'], 'run_id': old_run['run_id'],
        'payload_digest': old['payload_digest'], 'common_status': old_target['status'],
        'common_external_id': old_target['external_id'],
        'common_readback_evidence_digest': old_target['readback']['evidence_digest'],
        'common_readback_verified_at': old_target['readback']['verified_at'],
    }
    evidence = release_adapters.bind_native_common_readback(source, comparison)
    assert evidence['mode'] == 'readback_reuse_no_write'
    assert evidence['external_writes_performed'] == []
    store.record_target_success(reuse_run['run_id'], 'miaoshou:COMMON',
        external_id=payload['product_id'], readback_evidence=evidence)


@pytest.mark.parametrize('source_mode', ['retained', 'unknown'])
def test_real_getter_flow_source_and_census_do_not_grant_another_approval(live, monkeypatch, tmp_path, source_mode):
    _, task, _, _ = _native_common_chain(live, monkeypatch)
    store = server._release_store()
    original = common._read(server, task)
    payload = original['common']['plan']['payload']
    if source_mode == 'retained':
        _historical_write_and_reuse(store, payload, monkeypatch)
        # Current pending COMMON stays a technical preparation. Historical
        # rows share its real immutable root, but carry no current approval.
        current = store.create_plan(payload)
        assert current['status'] == 'PENDING_APPROVAL'
        assert store.active_plan_for_product(payload['product_id'])['plan_id'] == current['plan_id']
    view = common._read(server, task)
    technical = view['common']['technical_admission']
    assert technical['status'] == 'BLOCKED'
    assert technical['execution_authority'] is technical['final_review_available'] is False
    assert set(technical['authority_facts'].values()) == {'UNKNOWN'}
    assert 'COMMON_HISTORICAL_WRITE_BUDGET_UNKNOWN' in technical['blockers']
    assert view['external_writes_performed'] == []
    if source_mode == 'retained':
        assert technical['source_facts']['status'] == 'RETAINED_IDENTITY_VERIFIED'
        census = technical['source_facts']['native_local_census']
        assert census['status'] == 'LOCAL_PERSISTED_CENSUS'
        assert census['local_observed_confirmed_writes'] == census['local_observed_readonly_reuses'] == 1
        assert census['confirmed_write_count'] == census['budget_authority'] == 'UNKNOWN'
    else:
        assert technical['source_facts']['status'] == 'UNKNOWN'
        assert not technical['source_facts'].get('native_local_census')
    with connect(store) as db:
        before = rows(db)
    expected = tmp_path / 'actual-getter-before-browser.json'
    # Register only media declared by the actual owned dashboard/R2 documents.
    # The browser replaces these image bytes locally; no external read occurs.
    def media_urls(value):
        if isinstance(value, dict):
            return set().union(*(media_urls(part) for part in value.values()))
        if isinstance(value, list):
            return set().union(*(media_urls(part) for part in value))
        return {value} if isinstance(value, str) and value.startswith('https://') else set()
    fixture_media = media_urls(release_control.build_release_dashboard(offer_id=live['offer']))
    fixture_media |= media_urls(bridge.load_r2_documents(live['offer']))
    fixture_media = {url for url in fixture_media
                     if urlsplit(url).hostname in {'example.com', 'fixture.example'}
                     and Path(urlsplit(url).path).suffix.lower() in {'.png', '.jpg', '.jpeg', '.webp', '.svg'}}
    expected.write_text(json.dumps({'getter': view, 'test_only_media_urls': sorted(fixture_media)},
                                   ensure_ascii=False, indent=2), encoding='utf-8')
    available = _browser_runtime()
    assert available, 'Real Chromium runtime is required; no skip'
    node, modules = available
    script = Path(__file__).parent / 'browser/native_common_flow.cjs'
    result = subprocess.run([str(node), str(script),
        f'http://127.0.0.1:{live["port"]}', str(tmp_path), live['offer'],
        source_mode, str(expected)], env=dict(os.environ, NODE_PATH=str(modules)),
        capture_output=True, text=True, encoding='utf-8', timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
    proof = json.loads(result.stdout)
    assert proof['errors'] == [] and proof['posts'] == []
    assert proof['source_mode'] == source_mode and proof['viewport_widths'] == [1440, 390]
    assert proof['authority'] == 'UNKNOWN_SYNTHETIC_RETAINED_IO_NO_EXECUTION'
    with connect(store) as db:
        assert rows(db) == before
    final = common._read(server, task)
    assert final['common']['technical_admission'] == technical

