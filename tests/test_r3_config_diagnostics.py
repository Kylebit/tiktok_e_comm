import io
import json
from pathlib import Path
import hashlib
import os

import pytest

from modules.products import server
from test_b4b_common_stage import context


def handler_request(path, body=None):
    """Run the real Handler parser/router without allocating a network socket."""
    data = b'' if body is None else json.dumps(body).encode()
    method = 'GET' if body is None else 'POST'
    raw = (f'{method} {path} HTTP/1.1\r\nHost: 127.0.0.1:8765\r\n'
           f'Content-Type: application/json\r\nContent-Length: {len(data)}\r\n'
           'Connection: close\r\n\r\n').encode() + data
    class Connection:
        output = io.BytesIO()
        def makefile(self, *args, **kwargs):
            return io.BytesIO(raw)
        def sendall(self, value):
            self.output.write(value)
    connection = Connection()
    server.Handler(connection, ('127.0.0.1', 50001), object())
    head, response = connection.output.getvalue().split(b'\r\n\r\n', 1)
    return int(head.split(b' ')[1]), json.loads(response)


def test_default_both_missing_visible_before_common_via_real_handler(tmp_path, monkeypatch):
    from shared_platform.publication_runtime_config import capture_startup_config
    _, _, store, request = context(tmp_path, monkeypatch)
    monkeypatch.setattr(server, 'R3_STARTUP_CONFIG', capture_startup_config(root=tmp_path, environ={}))
    status, result = handler_request('/api/product-workspace/publication-stages?offer_id=' + request['offer_id'])
    assert status == 200, result
    assert result['common']['status'] == 'APPROVAL_REQUIRED'
    assert result['configuration']['status'] == 'BLOCKED'
    assert [x['status'] for x in result['configuration']['documents'].values()] == ['MISSING', 'MISSING']
    assert not store.path.exists()
    status, preview = handler_request('/api/product-workspace/r3-marketplace/preview', {'offer_id': request['offer_id']})
    assert status == 409 and preview['error'] == 'COMMON_VERIFIED_READBACK_REQUIRED'
    assert preview['configuration']['status'] == 'BLOCKED'
    assert str(tmp_path) not in json.dumps(preview)


def test_registry_invalid_preserves_valid_policy_and_no_private_content(tmp_path, monkeypatch):
    from shared_platform.publication_runtime_config import diagnose
    cfg = configured(tmp_path, monkeypatch)
    (tmp_path / 'incidents.json').write_text('{"schema_version":"PRIVATE-INVALID"}')
    public, private = diagnose(cfg)
    assert public['documents']['policy']['status'] == 'VALID'
    assert public['documents']['incident_registry']['status'] == 'INVALID'
    assert private == {} and 'PRIVATE-INVALID' not in json.dumps(public)


def test_overdeep_json_does_not_hide_other_document_diagnostic(tmp_path, monkeypatch):
    import sys
    from shared_platform.publication_runtime_config import diagnose
    cfg = configured(tmp_path, monkeypatch)
    depth = sys.getrecursionlimit() + 100
    (tmp_path / 'policy.json').write_text('[' * depth + '0' + ']' * depth)
    public, _ = diagnose(cfg)
    # JSON implementations may parse deep arrays iteratively. Either parse
    # refusal or the existing object contract must block without hiding registry.
    assert public['documents']['policy']['status'] in {'MALFORMED', 'INVALID'}
    assert public['status'] == 'BLOCKED'
    assert public['documents']['incident_registry']['status'] == 'VALID'


def test_file_replaced_during_open_is_rejected_before_content_read(tmp_path, monkeypatch):
    from shared_platform import publication_runtime_config as config
    cfg = configured(tmp_path, monkeypatch)
    original_open = config.os.open
    def replaced(path, flags):
        if Path(path).name == 'policy.json':
            Path(path).rename(tmp_path / 'old.json')
            Path(path).write_text('replaced-private')
        return original_open(path, flags)
    monkeypatch.setattr(config.os, 'open', replaced)
    public, _ = config.diagnose(cfg)
    assert public['documents']['policy']['status'] == 'CHANGED_DURING_READ'
    assert public['documents']['policy']['content_digest'] is None
    assert public['documents']['incident_registry']['status'] == 'VALID'


def configured(tmp_path, monkeypatch):
    from test_b4b_publication_preview import governed_files
    governed_files(tmp_path, monkeypatch)
    return server.R3_STARTUP_CONFIG


def test_unreadable_policy_preserves_registry_diagnostic(tmp_path, monkeypatch):
    from shared_platform import publication_runtime_config as config
    cfg = configured(tmp_path, monkeypatch)
    original = config.os.open
    def refused(path, flags):
        if Path(path).name == 'policy.json':
            raise PermissionError('synthetic private path must stay private')
        return original(path, flags)
    monkeypatch.setattr(config.os, 'open', refused)
    public, private = config.diagnose(cfg)
    assert public['documents']['policy']['status'] == 'UNREADABLE'
    assert public['documents']['policy']['content_digest'] is None
    assert public['documents']['incident_registry']['status'] == 'VALID'
    assert private == {} and 'synthetic private' not in json.dumps(public)


def test_owned_tcp_handler_configured_and_missing_do_not_approve(tmp_path, monkeypatch):
    import http.client
    import threading
    from http.server import ThreadingHTTPServer
    _, _, store, request = context(tmp_path, monkeypatch)
    configured(tmp_path, monkeypatch)
    httpd = ThreadingHTTPServer(('127.0.0.1', 0), server.Handler)
    worker = threading.Thread(target=httpd.serve_forever, daemon=True)
    worker.start()
    records = []
    def call(method, path, body=None):
        connection = http.client.HTTPConnection('127.0.0.1', httpd.server_port, timeout=5)
        try:
            connection.request(method, path, body=json.dumps(body) if body is not None else None,
                               headers={'Content-Type': 'application/json'})
            response = connection.getresponse()
            result = json.loads(response.read())
            records.append({'method': method, 'path': path, 'status': response.status, 'body': result})
            return response.status, result
        finally:
            connection.close()
    try:
        for expected in ('CONFIGURED', 'BLOCKED'):
            status, result = call('GET', '/api/product-workspace/publication-stages?offer_id=' + request['offer_id'])
            assert status == 200 and result['configuration']['status'] == expected
            assert result['common']['status'] == 'APPROVAL_REQUIRED'
            status, result = call('POST', '/api/product-workspace/r3-marketplace/preview', {'offer_id': request['offer_id']})
            assert status == 409 and result['error'] == 'COMMON_VERIFIED_READBACK_REQUIRED'
            assert result['configuration']['status'] == expected
            assert str(tmp_path) not in json.dumps(result) and 'paid_models' not in json.dumps(result)
            if expected == 'CONFIGURED':
                (tmp_path / 'policy.json').unlink()
        assert not store.path.exists()
        from test_b4b_publication_preview import reviewed_marketplace
        _, _, market_store, data, transport, market = reviewed_marketplace(tmp_path / 'verified', monkeypatch)
        status, result = call('POST', '/api/product-workspace/r3-marketplace/preview', {'offer_id': data['offer_id']})
        assert status == 200 and result['marketplace']['preview']['status'] == 'READY_FOR_FINAL_REVIEW'
        binding = result['marketplace']['plan']['payload']['r3_marketplace_binding']
        assert binding['policy']['redacted'] is True and binding['incident_registry']['redacted'] is True
        assert result['marketplace']['plan']['payload_digest'] == market['plan']['payload_digest']
        assert 'paid_models' not in json.dumps(result) and str(tmp_path) not in json.dumps(result)
        assert not market_store.get_plan(data['plan_id']) and transport.mutations == 1
    finally:
        httpd.shutdown(); worker.join(timeout=3); httpd.server_close()
        (tmp_path / 'owned-tcp.json').write_text(json.dumps({'port': httpd.server_port, 'requests': records}, indent=2))


@pytest.mark.parametrize('damage,expected', [
    ('missing', 'MISSING'), ('json', 'MALFORMED'), ('encoding', 'MALFORMED'),
    ('schema', 'INVALID'), ('inactive', 'AUTHORITY_UNCONFIRMED'),
    ('authority', 'AUTHORITY_UNCONFIRMED'), ('budget', 'INVALID'),
])
def test_bad_policy_keeps_independent_registry_diagnostic(tmp_path, monkeypatch, damage, expected):
    from shared_platform.publication_runtime_config import diagnose
    cfg = configured(tmp_path, monkeypatch)
    p = tmp_path / 'policy.json'
    value = json.loads(p.read_text())
    if damage == 'missing': p.unlink()
    elif damage == 'json': p.write_text('{ private path: SECRET')
    elif damage == 'encoding': p.write_bytes(b'\xff')
    else:
        if damage == 'schema': value['schema_version'] = 'other'
        elif damage == 'inactive': value['status'] = 'INACTIVE'
        elif damage == 'authority': value.pop('authority')
        else: value['paid_models']['maximum_confirmed_requests_per_product'] = -1
        p.write_text(json.dumps(value))
    public, values = diagnose(cfg)
    assert public['documents']['policy']['status'] == expected
    assert public['documents']['incident_registry']['status'] == 'VALID'
    assert values == {} and public['status'] == 'BLOCKED'
    assert str(tmp_path) not in json.dumps(public) and 'SECRET' not in json.dumps(public)


@pytest.mark.parametrize('relative', ['../outside.json', '/outside.json', 'C:\\outside.json', '..\\outside.json', 'policy.json:stream'])
def test_path_escape_rejected_before_open(tmp_path, monkeypatch, relative):
    from shared_platform import publication_runtime_config as config
    cfg = config.capture_startup_config(root=tmp_path, environ={
        'ORBIT_R3_POLICY_PATH': relative, 'ORBIT_R3_INCIDENT_REGISTRY_PATH': relative})
    def forbidden(*args, **kwargs): raise AssertionError('unsafe path opened')
    monkeypatch.setattr(config.os, 'open', forbidden)
    public, _ = config.diagnose(cfg)
    assert {x['status'] for x in public['documents'].values()} == {'UNSAFE_PATH'}


@pytest.mark.parametrize('kind', ['file', 'directory', 'root', 'dangling'])
def test_links_rejected_before_content_open(tmp_path, monkeypatch, kind):
    from shared_platform import publication_runtime_config as config
    safe = tmp_path / 'safe'; safe.mkdir()
    outside = tmp_path / 'outside'; outside.mkdir()
    (outside / 'policy.json').write_text('private must not be read')
    root, relative = safe, 'policy.json'
    if kind == 'root':
        root = tmp_path / 'root-link'; root.symlink_to(outside, target_is_directory=True)
    elif kind == 'directory':
        (safe / 'link').symlink_to(outside, target_is_directory=True); relative = 'link/policy.json'
    else:
        (safe / 'policy.json').symlink_to(outside / ('absent.json' if kind == 'dangling' else 'policy.json'))
    cfg = config.capture_startup_config(root=root, environ={
        'ORBIT_R3_POLICY_PATH': relative, 'ORBIT_R3_INCIDENT_REGISTRY_PATH': relative})
    def forbidden(*args, **kwargs): raise AssertionError('linked file opened')
    monkeypatch.setattr(config.os, 'open', forbidden)
    public, _ = config.diagnose(cfg)
    assert {x['status'] for x in public['documents'].values()} == {'UNSAFE_PATH'}


def test_startup_capture_ignores_later_environment_and_http_override(tmp_path, monkeypatch):
    from shared_platform import publication_runtime_config as config
    _, _, store, request = context(tmp_path, monkeypatch)
    cfg = configured(tmp_path, monkeypatch)
    monkeypatch.setenv('ORBIT_R3_POLICY_PATH', '../private.json')
    status, result = handler_request('/api/product-workspace/r3-marketplace/preview', {
        'offer_id': request['offer_id'], 'policy_path': '../private.json',
        'configuration': {'status': 'CONFIGURED'}, 'policy': {'status': 'ACTIVE'}})
    assert status == 409 and result['error'] == 'COMMON_VERIFIED_READBACK_REQUIRED'
    assert result['configuration'] == config.diagnose(cfg)[0]
    assert result['configuration']['status'] == 'CONFIGURED'
    status, _ = handler_request('/api/product-workspace/publication-stages?offer_id=' + request['offer_id'] + '&policy_path=private.json')
    assert status == 400 and not store.path.exists()


def test_real_compiler_explicit_config_digests_and_redacted_http(tmp_path, monkeypatch):
    from test_b4b_publication_preview import reviewed_marketplace
    _, _, store, data, io, market = reviewed_marketplace(tmp_path, monkeypatch)
    status, result = handler_request('/api/product-workspace/r3-marketplace/preview', {'offer_id': data['offer_id']})
    assert status == 200 and result['marketplace']['preview']['status'] == 'READY_FOR_FINAL_REVIEW'
    assert result['configuration']['documents']['policy']['content_digest'] == hashlib.sha256((tmp_path / 'policy.json').read_bytes()).hexdigest()
    binding = result['marketplace']['plan']['payload']['r3_marketplace_binding']
    assert binding['policy']['redacted'] is True and binding['incident_registry']['redacted'] is True
    assert 'paid_models' not in json.dumps(result)
    assert result['marketplace']['plan']['payload_digest'] == market['plan']['payload_digest']
    assert not store.get_plan(data['plan_id']) and io.mutations == 1


def test_config_drift_requires_new_preview_without_granting_approval(tmp_path, monkeypatch):
    from test_b4b_publication_preview import reviewed_marketplace
    monkeypatch.setattr(server, '_r3_marketplace_business_execution_gate', lambda: None)
    _, _, store, data, io, _ = reviewed_marketplace(tmp_path, monkeypatch)
    p = tmp_path / 'policy.json'; value = json.loads(p.read_text())
    value['paid_models']['maximum_confirmed_requests_per_product'] += 1
    p.write_text(json.dumps(value))
    status, result = handler_request('/api/product-workspace/r3-marketplace/approve', data)
    assert status == 409 and result['error'] == 'MARKETPLACE_REVIEW_IDENTITY_CONFLICT'
    assert not store.get_plan(data['plan_id']) and io.mutations == 1


def test_pending_marketplace_plan_is_blocked_if_configuration_disappears(tmp_path, monkeypatch):
    from test_b4b_publication_preview import reviewed_marketplace
    _, _, store, data, io, market = reviewed_marketplace(tmp_path, monkeypatch)
    store.create_plan(market['plan']['payload'])
    (tmp_path / 'policy.json').unlink()
    status, result = handler_request('/api/product-workspace/publication-stages?offer_id=' + data['offer_id'])
    assert status == 200 and result['marketplace']['status'] == 'RECONCILIATION_REQUIRED'
    assert result['marketplace']['blockers'] == ['R3_CONFIG_POLICY_MISSING']
    assert store.get_plan(data['plan_id'])['status'] == 'PENDING_APPROVAL'
    assert io.mutations == 1


def test_approved_recovery_uses_frozen_policy_after_current_file_missing(tmp_path, monkeypatch):
    from test_b4b_publication_preview import reviewed_marketplace, seed_preexisting_marketplace_approval
    from shared_platform import publication_autopilot as authority
    monkeypatch.setattr(server, '_r3_marketplace_business_execution_gate', lambda: None)
    _, _, store, data, io, market = reviewed_marketplace(tmp_path, monkeypatch)
    seed_preexisting_marketplace_approval(store, data, market)
    persist = authority.persist_final_approval_receipt
    def interrupted(*args, **kwargs): raise OSError('synthetic receipt interrupted')
    monkeypatch.setattr(authority, 'persist_final_approval_receipt', interrupted)
    assert server._approve_r3_marketplace_stage(data)[0] == 409
    approved = store.get_plan(data['plan_id'])
    (tmp_path / 'policy.json').unlink()
    monkeypatch.setattr(authority, 'persist_final_approval_receipt', persist)
    status, result = handler_request('/api/product-workspace/r3-marketplace/resume-binding', {
        'offer_id': data['offer_id'], 'plan_id': data['plan_id']})
    assert status == 200, result
    assert result['marketplace']['status'] == 'READY_TO_PUBLISH'
    assert store.get_plan(data['plan_id'])['approval'] == approved['approval']
    assert io.mutations == 1
    status, result = handler_request('/api/product-workspace/publication-stages?offer_id=' + data['offer_id'])
    assert result['configuration']['status'] == 'BLOCKED'
    assert result['marketplace']['status'] == 'READY_TO_PUBLISH'


def test_unknown_common_stays_reconciliation_with_missing_configuration(tmp_path, monkeypatch):
    from test_b4b_common_stage import CommonTransport, approve
    _, _, store, request = context(tmp_path, monkeypatch)
    io = CommonTransport(monkeypatch, timeout=True)
    exact = approve(request)
    assert server._prepare_miaoshou_release(dict(exact, confirm_miaoshou_write=True))[0] != 200
    for _ in range(2):
        _, result = handler_request('/api/product-workspace/publication-stages?offer_id=' + request['offer_id'])
        assert result['configuration']['status'] == 'BLOCKED'
        assert result['common']['status'] == 'RECONCILIATION_REQUIRED'
    assert io.mutations == 1


def test_marketplace_unknown_keeps_existing_run_when_config_disappears(tmp_path, monkeypatch):
    from test_b4b_publication_preview import reviewed_marketplace, seed_preexisting_marketplace_approval
    from test_product_publication_report_store import _report_payload
    _, _, store, data, io, market = reviewed_marketplace(tmp_path, monkeypatch)
    seed_preexisting_marketplace_approval(store, data, market)
    assert server._approve_r3_marketplace_stage(data)[0] == 200
    snapshot = store.approved_publication_snapshot(offer_id=data['offer_id'], plan_id=data['plan_id'])
    runs = server._product_publication_run_store()
    run = runs.create_run(run_id='config-unknown', offer_id=data['offer_id'], revision=snapshot['product_revision'],
        plan_id=data['plan_id'], snapshot_digest=snapshot['snapshot_digest'], platform_scope=('OZON',), target_count=1)
    runs.mark_running(run_id=run.run_id)
    report = _report_payload(run_id=run.run_id, report_id=run.report_id, offer_id=data['offer_id'],
        revision=snapshot['product_revision'], status='FAILED')
    report.update(schema_version='product-publication-report/v2', plan_id=data['plan_id'],
        execution_identity={'skill_digest':'0'*64,'git_commit':'0'*40,'code_digest':'0'*64},
        targets=[{'target_label':'ozon:RU','status':'FAILED','evidence':{'target_label':'ozon:RU','status':'FAILED',
            'stage':'PUBLISH','provider_code':'OUTCOME_UNKNOWN','provider_reason':'synthetic response lost',
            'request_attempted':True,'outcome_unknown':True,'external_write_count':None}}])
    report['snapshot']['digest'] = snapshot['snapshot_digest']
    report['summary']['platforms'] = [{'platform':'OZON','status':'FAILED','target_count':1,
        'verified_count':0,'processing_count':0,'failed_count':1}]
    report['summary']['evidence'].update(external_write_count=0, readback_completed=False)
    report['summary']['requires_human_action'] = True
    server._product_publication_report_store().store_report(report)
    (tmp_path / 'policy.json').unlink()
    for _ in range(2):
        status, result = handler_request('/api/product-workspace/publication-stages?offer_id=' + data['offer_id'])
        assert status == 200 and result['configuration']['status'] == 'BLOCKED'
        assert result['marketplace']['status'] == 'RECONCILIATION_REQUIRED'
        ozon = next(row for row in result['marketplace']['platforms'] if row['platform'] == 'OZON')
        assert ozon['run_id'] == run.run_id and ozon['next_action'] == 'RECONCILE_EXISTING_RUN'
    assert io.mutations == 1
