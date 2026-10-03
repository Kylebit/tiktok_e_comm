"""Actual offer-only GET and rendered completed/unknown baseline, without grants."""
import http.client
import json
import os
from pathlib import Path
import subprocess
from urllib.parse import urlencode, urlsplit

import pytest

from shared_platform import native_common_technical_execution as technical
from shared_platform import release_control, publication_r3_image_bridge as bridge
from test_round1_workspace_freeze import live
from test_native_common_retained_review_graph import _completed, _market
from test_native_common_baseline_source import _service_config
from test_native_common_baseline_getter import _no_new_write_or_current_read
from test_release_ux_contract import _browser_runtime
from test_r3_common_source_facts import connect, rows


@pytest.mark.parametrize('retained', [True, False], ids=['retained', 'unknown'])
def test_real_offer_only_flow_renders_completed_baseline_or_specific_unknown_without_another_review(live, monkeypatch, tmp_path, retained):
    store, plan, payload, facts, run_id = _completed(live, monkeypatch, tmp_path)
    market = _market(store, plan, run_id)
    _service_config(tmp_path, monkeypatch)
    if not retained:
        with technical._existing_transaction(store) as db:
            db.execute('UPDATE release_target_readbacks SET evidence_json=? WHERE run_id=?', ('{}', run_id))
    _no_new_write_or_current_read(monkeypatch)
    with connect(store) as db:
        before = rows(db)
    connection = http.client.HTTPConnection('127.0.0.1', live['port'], timeout=15)
    try:
        connection.request('GET', '/api/product-workspace/publication-stages?' + urlencode({'offer_id':live['offer']}))
        response = connection.getresponse()
        assert response.status == 200
        getter = json.loads(response.read())
    finally:
        connection.close()
    assert getter['marketplace']['plan']['plan_id'] == market['plan_id']
    assert getter['common']['status'] == ('RETAINED_TECHNICAL_BASELINE' if retained else 'TECHNICAL_CONDITIONS_UNKNOWN')
    assert getter['common']['user_status']['common_review_needed'] is False
    assert getter['marketplace']['execution_authority'] is getter['marketplace']['final_review_available'] is False
    assert getter['external_writes_performed'] == []
    def urls(value):
        if isinstance(value, dict): return set().union(*(urls(part) for part in value.values()))
        if isinstance(value, list): return set().union(*(urls(part) for part in value))
        return {value} if isinstance(value,str) and value.startswith('https://') else set()
    media = urls(release_control.build_release_dashboard(offer_id=live['offer'])) | urls(bridge.load_r2_documents(live['offer'])) | urls(getter)
    media = {url for url in media if urlsplit(url).hostname in {'example.com','fixture.example'}
             and Path(urlsplit(url).path).suffix.lower() in {'.png','.jpg','.jpeg','.webp','.svg'}}
    expected = tmp_path/'actual-offer-only-getter.json'
    expected.write_text(json.dumps({'getter':getter,'test_only_media_urls':sorted(media)},ensure_ascii=False),encoding='utf-8')
    available = _browser_runtime()
    assert available, 'Real Chromium runtime is required; no skip'
    node, modules = available
    result = subprocess.run([str(node),str(Path(__file__).parent/'browser/native_common_baseline_flow.cjs'),
        f'http://127.0.0.1:{live["port"]}',str(tmp_path),live['offer'],
        'retained' if retained else 'unknown',str(expected)],
        env=dict(os.environ,NODE_PATH=str(modules)),capture_output=True,text=True,encoding='utf-8',timeout=90)
    assert result.returncode == 0, result.stdout+result.stderr
    proof=json.loads(result.stdout)
    assert proof['errors'] == proof['posts'] == []
    assert proof['viewport_widths'] == [1440,390]
    assert proof['retained'] is retained and proof['offer_only_requests'] > 0
    assert proof['authority'] == 'RETAINED_LOCAL_FACTS_NO_EXECUTION_AUTHORITY'
    with connect(store) as db:
        assert rows(db) == before
