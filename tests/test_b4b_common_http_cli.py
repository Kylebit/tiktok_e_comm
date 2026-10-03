import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError

import pytest

from modules.products import server
from test_b4b_publication_preview import multivariant_context

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def api(tmp_path, monkeypatch):
    context = multivariant_context(tmp_path, monkeypatch)
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{httpd.server_port}', context
    finally:
        httpd.shutdown()
        thread.join(timeout=3)
        httpd.server_close()


def request(base, path, body=None):
    req = Request(base + path, data=None if body is None else json.dumps(body).encode(),
                  headers={'Content-Type': 'application/json'})
    try:
        response = urlopen(req, timeout=5)
    except HTTPError as error:
        response = error
    with response:
        return response.status, json.load(response)


def cli(script, base, *args):
    guard = str(ROOT / 'tests/fixtures/b4b_cli_guard.py')
    result = subprocess.run([sys.executable, '-B', guard, str(ROOT / script), '--base-url', base, *args],
                            capture_output=True, text=True, encoding='utf-8', timeout=15,
                            env={**os.environ, 'PYTHONIOENCODING': 'cp936:strict'})
    assert result.returncode in {0, 2}, result.stderr
    assert result.stdout, result.stderr
    return result.returncode, json.loads(result.stdout)


def test_real_http_and_default_workflow_cli_use_one_common_contract(api):
    base, (_, _, store, data, io) = api
    code, state = cli('scripts/product_publication_workflow.py', base, '--offer-id', data['offer_id'])
    assert code == 0 and state['stage'] == 'COMMON_TECHNICAL_ADMISSION_BLOCKED', state
    assert state['next_command'] is None
    assert not store.path.exists() and io.mutations == 0
    status, preview = request(base, '/api/product-workspace/r3-common/preview', data)
    assert status == 200, preview
    plan = preview['common']['plan']
    exact = {**data, 'plan_id': plan['plan_id'], 'confirmation_token': plan['confirmation_token']}
    assert request(base, '/api/product-workspace/miaoshou-draft/commit', {**exact, 'confirm_miaoshou_write': True})[0] == 409
    assert request(base, '/api/product-workspace/release-plan/approve', {**exact, 'user_approved': True, 'approved_by': 'Kyle'})[0] == 409
    assert store.get_plan(plan['plan_id']) is None
    code, result = cli('skills/publish-approved-product/scripts/prepare_publication_execution.py', base,
        '--offer-id', data['offer_id'], '--execute-miaoshou', '--confirm-miaoshou-write',
        '--plan-id', plan['plan_id'], '--confirmation-token', plan['confirmation_token'])
    assert code == 2 and result['ok'] is False, result
    assert io.mutations == 0 and io.reads == 0
    code, state = cli('scripts/product_publication_workflow.py', base, '--offer-id', data['offer_id'])
    assert state['stage'] == 'COMMON_TECHNICAL_ADMISSION_BLOCKED', state
    assert state['next_command'] is None


def test_http_previews_keep_origin_and_scope_guards(api):
    base, (_, _, _, data, io) = api
    req = Request(base + '/api/product-workspace/r3-common/preview', data=json.dumps(data).encode(),
                  headers={'Content-Type': 'application/json', 'Origin': 'https://foreign.example'})
    with pytest.raises(HTTPError) as error:
        urlopen(req, timeout=5)
    assert error.value.code in {400, 403}
    assert request(base, '/api/product-workspace/r3-common/preview',
                   {**data, 'publication_targets': ['miaoshou:COMMON', 'tiktok:LH_PH']})[0] == 409
    assert io.mutations == 0


def test_cli_unknown_common_write_response_is_reported_once_without_retry(monkeypatch, capsys):
    import importlib.util
    from shared_platform import publication_r3_image_bridge as bridge

    path = ROOT / 'skills/publish-approved-product/scripts/prepare_publication_execution.py'
    spec = importlib.util.spec_from_file_location('b4b_cli_unknown_contract', path)
    entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(entry)
    requests = []

    def lost_response(endpoint, *, data, base_url):
        requests.append((endpoint, data, base_url))
        return 0, {'ok': False, 'error': 'PRODUCT_CENTER_TRANSPORT_UNAVAILABLE',
                   'outcome_unknown': True, 'automatic_retry': False}

    monkeypatch.setattr(bridge, 'request_publication_stage', lost_response)
    exit_code = entry.main(['--offer-id', '9000052', '--base-url', 'http://127.0.0.1:59999',
        '--execute-miaoshou', '--confirm-miaoshou-write',
        '--plan-id', 'r3-common:9000052:exact', '--confirmation-token', 'PUBLISH-EXACT'])
    result = json.loads(capsys.readouterr().out)
    assert exit_code == 2 and result['http_status'] == 0
    assert result['outcome_unknown'] is True and result['automatic_retry'] is False
    assert len(requests) == 1
    assert requests[0][0] == '/api/product-workspace/miaoshou-draft/commit'
    assert requests[0][1]['plan_id'] == 'r3-common:9000052:exact'


@pytest.mark.parametrize('document,stage', [('round1_snapshot', 'FIRST_ROUND_REQUIRED'), ('image_qa', 'SECOND_ROUND_REQUIRED')])
def test_default_workflow_reads_actual_missing_preparation_evidence(api, document, stage):
    from shared_platform import publication_r3_image_bridge as bridge
    base, (_, _, store, data, io) = api
    (bridge.REPORTS_ROOT / data['offer_id'] / bridge.R2_DOCUMENTS[document]).unlink()
    code, result = cli('scripts/product_publication_workflow.py', base, '--offer-id', data['offer_id'])
    assert code == 0 and result['stage'] == stage, result
    assert '--execute' not in (result['next_command'] or '')
    assert not store.path.exists() and io.mutations == 0


def test_default_workflow_keeps_unknown_original_run_after_valid_r2_changes(api):
    from test_b4b_common_stage import approve
    from shared_platform import publication_r3_image_bridge as bridge
    from shared_platform.release_store import ReleaseAuthorizationError
    base, (_, dashboard, store, data, io) = api
    io.timeout = True
    exact = approve(data)
    assert request(base, '/api/product-workspace/miaoshou-draft/commit', {**exact, 'confirm_miaoshou_write': True})[0] != 200
    fixture = Path(__file__).parent / 'fixtures/b4b_common_actual'
    dashboard.clear()
    dashboard.update(json.loads((fixture / 'source-dashboard.json').read_text(encoding='utf-8')))
    for filename in bridge.R2_DOCUMENTS.values():
        (bridge.REPORTS_ROOT / data['offer_id'] / filename).write_bytes((fixture / filename).read_bytes())
    _, result = cli('scripts/product_publication_workflow.py', base, '--offer-id', data['offer_id'])
    assert result['stage'] == 'COMMON_RECONCILIATION_REQUIRED', result
    assert result['next_command'] is None
    assert result['common']['reconciliation_reference']['plan_id'] == exact['plan_id']
    with pytest.raises(ReleaseAuthorizationError, match='COMMON_RECONCILIATION_REQUIRED'):
        store.supersede_plan(exact['plan_id'])
    assert io.mutations == 1


def test_default_cli_recovers_only_the_persisted_final_approval_binding(api, monkeypatch):
    monkeypatch.setattr(server, '_r3_marketplace_business_execution_gate', lambda: None)
    from shared_platform import publication_autopilot as authority
    from test_b4b_publication_preview import seed_preexisting_marketplace_approval
    from test_b4b_common_stage import approve
    base, (_, _, store, data, io) = api
    exact = approve(data)
    assert request(base, '/api/product-workspace/miaoshou-draft/commit', {**exact, 'confirm_miaoshou_write': True})[0] == 200
    _, view = request(base, '/api/product-workspace/r3-marketplace/preview', {'offer_id': data['offer_id']})
    market = view['marketplace']
    plan = market['plan']
    seed_preexisting_marketplace_approval(store, {
        'offer_id': data['offer_id'], 'plan_id': plan['plan_id'],
        'confirmation_token': plan['confirmation_token']}, market)
    persist = authority.persist_final_approval_receipt
    def interrupted(*args, **kwargs):
        raise OSError('synthetic lost derived approval receipt')
    monkeypatch.setattr(authority, 'persist_final_approval_receipt', interrupted)
    status, result = request(base, '/api/product-workspace/r3-marketplace/approve', {
        'offer_id': data['offer_id'], 'plan_id': plan['plan_id'], 'confirmation_token': plan['confirmation_token'],
        'preview_digest': market['preview']['candidate_digest'], 'user_approved': True, 'approved_by': 'Kyle'})
    assert status == 409, result
    approval_id = store.get_plan(plan['plan_id'])['approval']['approval_id']
    _, state = cli('scripts/product_publication_workflow.py', base, '--offer-id', data['offer_id'])
    assert state['stage'] == 'MARKETPLACE_APPROVAL_BINDING_REQUIRED', state
    assert state['approval_recorded'] is True
    assert '--resume-approval-binding' in state['next_command'] and '--approve-marketplace' not in state['next_command']
    monkeypatch.setattr(authority, 'persist_final_approval_receipt', persist)
    code, result = cli('skills/publish-approved-product/scripts/prepare_publication_execution.py', base,
        '--offer-id', data['offer_id'], '--plan-id', plan['plan_id'], '--resume-approval-binding')
    assert code == 0, result
    assert store.get_plan(plan['plan_id'])['approval']['approval_id'] == approval_id
    assert io.mutations == 1


@pytest.mark.parametrize('outcome', ['unknown', 'failed', 'missing_report'])
def test_http_final_approval_and_async_outcome_are_separate_from_common(api, outcome, monkeypatch):
    monkeypatch.setattr(server, '_r3_marketplace_business_execution_gate', lambda: None)
    from test_b4b_common_stage import approve
    from test_b4b_publication_preview import seed_preexisting_marketplace_approval
    from test_product_publication_report_store import _report_payload
    base, (_, _, store, common_request, io) = api
    exact = approve(common_request)
    assert request(base, '/api/product-workspace/miaoshou-draft/commit', {**exact, 'confirm_miaoshou_write': True})[0] == 200
    _, view = request(base, '/api/product-workspace/r3-marketplace/preview', {'offer_id': common_request['offer_id']})
    market = view['marketplace']
    plan = market['plan']
    seed_preexisting_marketplace_approval(store, {
        'offer_id': common_request['offer_id'], 'plan_id': plan['plan_id'],
        'confirmation_token': plan['confirmation_token']}, market)
    code, approved = cli('skills/publish-approved-product/scripts/prepare_publication_execution.py', base,
        '--offer-id', common_request['offer_id'], '--plan-id', plan['plan_id'],
        '--resume-approval-binding')
    assert code == 0, approved
    _, state = cli('scripts/product_publication_workflow.py', base, '--offer-id', common_request['offer_id'])
    assert state['stage'] == 'MARKETPLACE_READY_TO_PUBLISH', state
    assert '--execute' in state['next_command']
    snapshot = store.approved_publication_snapshot(offer_id=common_request['offer_id'], plan_id=plan['plan_id'])
    from shared_platform import publication_autopilot as authority, publication_r3_image_bridge as bridge
    candidate, receipt = authority.resolve_persisted_execution_authority(snapshot=snapshot,
        platform_scope=tuple(sorted({label.split(':')[0].upper() for label in plan['targets']})),
        target_labels=tuple(plan['targets']), reports_root=bridge.REPORTS_ROOT)
    runs = server._product_publication_run_store()
    identity = {'skill_digest': '0'*64, 'git_commit': '0'*40, 'code_digest': '0'*64}
    created = runs.create_run(run_id='r3-existing-unknown', offer_id=common_request['offer_id'],
        revision=snapshot['product_revision'], plan_id=plan['plan_id'], snapshot_digest=snapshot['snapshot_digest'],
        platform_scope=('OZON',), target_count=1, execution_identity=identity)
    runs.mark_running(run_id=created.run_id)
    _, state = cli('scripts/product_publication_workflow.py', base, '--offer-id', common_request['offer_id'])
    assert state['common']['status'] == 'VERIFIED' and state['stage'] == 'MARKETPLACE_PROCESSING', state
    report = _report_payload(run_id=created.run_id, report_id=created.report_id, offer_id=common_request['offer_id'],
                             revision=snapshot['product_revision'], status='FAILED')
    report.update(schema_version='product-publication-report/v2', plan_id=plan['plan_id'],
        execution_identity=identity,
        release_authorization={'candidate_digest':candidate['candidate_digest'],
                               'approval_digest':receipt['approval_digest']},
        targets=[{'target_label': 'ozon:RU', 'status': 'FAILED', 'evidence': {'target_label': 'ozon:RU', 'status': 'FAILED',
            'stage': 'PUBLISH', 'provider_code': 'VALIDATION_FAILED' if outcome == 'failed' else 'OUTCOME_UNKNOWN',
            'provider_reason': 'Synthetic bounded result',
            'request_attempted': True, 'outcome_unknown': outcome == 'unknown', 'external_write_count': None}}])
    report['snapshot']['digest'] = snapshot['snapshot_digest']
    report['summary']['platforms'] = [{'platform': 'OZON', 'status': 'FAILED', 'target_count': 1,
        'verified_count': 0, 'processing_count': 0, 'failed_count': 1}]
    report['summary']['evidence'].update(external_write_count=0, readback_completed=False)
    report['summary']['requires_human_action'] = True
    if outcome == 'missing_report':
        runs.mark_failed(run_id=created.run_id, failure_code='WORKER_STOPPED')
    else:
        server._product_publication_report_store().store_report(report)
    if outcome == 'unknown':
        from shared_platform import publication_r3_image_bridge as bridge
        (bridge.REPORTS_ROOT / common_request['offer_id'] / bridge.R2_DOCUMENTS['image_qa']).unlink()
    code, state = cli('scripts/product_publication_workflow.py', base, '--offer-id', common_request['offer_id'])
    # Ozon failed while the separately approved TikTok target is still NOT_RUN.
    expected = 'MARKETPLACE_PARTIAL' if outcome == 'failed' else 'MARKETPLACE_RECONCILIATION_REQUIRED'
    assert state['stage'] == expected, state
    assert code == (0 if outcome == 'failed' else 2), state
    assert state['common']['status'] == 'VERIFIED'
    if outcome == 'failed':
        targets={row['target_label']:row for row in state['marketplace']['target_results']}
        assert targets['ozon:RU']['status']=='FAILED'
        assert targets['tiktok:LH_PH']['status']=='NOT_RUN'
        assert '--platform tiktok' in state['next_command']
        assert '--finalize-release-handoff' not in state['next_command']
    else:
        assert state['next_command'] is None
    assert io.mutations == 1
