"""Service call-site receipt regression; synthetic credentials and local DB only.

The closed transport accepts exactly COMMON detail/edit requests. No production
configuration, account authority, Offer budget or marketplace executor is used.
"""
from copy import deepcopy
import base64
import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from modules.miaoshou import client
from modules.products import release_adapters, server
from shared_platform import publication_common_write_admission, publication_runtime_config
from shared_platform import release_control, release_store
from test_product_release_v1 import _dashboard, _request, _successor_dashboard


CONFIG = {'app_id': 'closed-service-app', 'app_secret': 'closed-service-secret'}


class ClosedResponse:
    status = 200
    headers = {'Content-Encoding': 'identity'}

    def __init__(self, url, raw):
        self.url, self.raw = url, raw

    def geturl(self):
        return self.url

    def read(self, size=-1):
        return self.raw if size < 0 else self.raw[:size]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class ClosedCommonTransport:
    def __init__(self, monkeypatch, payload):
        self.reads, self.edits, self.observer_reads = [], [], []
        self.responses = []
        self.detail = self.detail_for(payload)
        self.timeout_after_edit = False
        self.damage_after_edit = False
        monkeypatch.setattr(client.urllib.request, 'urlopen', self.open)
        monkeypatch.setattr(client.urllib.request, 'build_opener',
                            lambda *handlers: SimpleNamespace(open=self.observe_open))
        monkeypatch.setattr(client, '_wait_for_open_slot', lambda: None)
        monkeypatch.setattr(client, '_load_config', lambda: dict(CONFIG))

    @staticmethod
    def detail_for(payload):
        detail = release_adapters._immutable_miaoshou_common_draft(payload)
        facts = payload['product_facts']
        commercial = {key.strip(';'): value for key, value in facts['sku_commercial_facts'].items()}
        assignment = {row['variant_key'].strip(';'): row['model_sku']
                      for row in payload['sku_lineage']['assignment']['model_skus']}
        selected = {row['key'].strip(';'): row for row in facts['selected_skus']}
        detail['skuMap'] = {
            key: {'itemNum': assignment[key],
                  'specLabel': facts['sku_label_overrides'].get(key) or selected[key]['label'],
                  'weight': float(commercial[key]['weight_kg']),
                  **dict(zip(('packageLength', 'packageWidth', 'packageHeight'),
                             map(float, commercial[key]['package_cm'])))}
            for key in assignment
        }
        return detail

    def observe_open(self, request, timeout):
        assert request.full_url == client.OPEN_BASE_URL + client.COMMON_DETAIL_OBSERVATION_PATH
        self.observer_reads.append(json.loads(request.data))
        return self.open(request, timeout)

    def open(self, request, timeout):
        assert request.get_method() == 'POST' and timeout == 30
        assert request.get_header('X-app-key') == CONFIG['app_id']
        body = json.loads(request.data)
        detail_url = client.OPEN_BASE_URL + release_adapters.MIAOSHOU_COMMON_DETAIL_PATH
        edit_url = client.OPEN_BASE_URL + release_adapters.MIAOSHOU_COMMON_EDIT_PATH
        assert request.full_url in {detail_url, edit_url}, 'closed transport refuses any other URL'
        assert body['commonCollectBoxDetailId'] == int(self.detail['commonCollectBoxDetailId'])
        if request.full_url == edit_url:
            self.edits.append(deepcopy(body))
            self.detail = deepcopy(body['editCommonCollectBoxDetail'])
            if self.timeout_after_edit:
                raise TimeoutError('closed edit response lost after dispatch')
            raw = b'{"result":"success"}'
        else:
            self.reads.append(deepcopy(body))
            detail = deepcopy(self.detail)
            if self.damage_after_edit and self.edits:
                detail['title'] = 'closed stale provider title'
            raw = json.dumps({'result': 'success', 'data': {
                'editCommonCollectBoxDetail': detail, 'ossMd5': 'closed-md5'}},
                ensure_ascii=False, indent=2).encode('utf-8')
            self.responses.append(raw)
        return ClosedResponse(request.full_url, raw)


def context(tmp_path, monkeypatch, *, authority=True):
    monkeypatch.setattr(server, 'ROOT', tmp_path)
    startup = publication_runtime_config.capture_startup_config(root=tmp_path, environ={})
    monkeypatch.setattr(server, 'R3_STARTUP_CONFIG', startup)
    store = release_store.ReleaseStore(tmp_path / 'closed-release.db')
    monkeypatch.setattr(release_store, 'default_release_store', lambda: store)
    dashboard = _dashboard()
    monkeypatch.setattr(release_control, 'build_release_dashboard', lambda **kw: dashboard)
    if authority:
        # Isolated historical-write mechanics, explicitly not native standing policy.
        monkeypatch.setattr(publication_common_write_admission, 'inspect_common_write_admission',
            lambda plan, **context: {'status': 'READY', 'blockers': [], 'binding': {
                'offer_id': plan['product_id'], 'plan_id': plan['plan_id'],
                'payload_digest': plan['payload_digest']}, 'receipt_digest': 'closed-test-only'})
    view = server._product_workspace_view(dashboard)
    request = _request(view)
    status, result = server._approve_release_plan_locally(
        dict(request, user_approved=True, approved_by='Kyle'))
    assert status == 200, result
    payload = store.get_plan(request['plan_id'])['payload']
    io = ClosedCommonTransport(monkeypatch, payload)
    factory_calls = []

    def factory():
        factory_calls.append('service-owned-read-only')
        return client.NativeCommonDetailObserver(CONFIG)

    # This is a trusted in-process service dependency, never an HTTP field.
    # Baseline 75 ignores it and walks its real legacy transport/call sites;
    # missing raw receipts below are the regression, not a mocked dict result.
    monkeypatch.setattr(server, '_COMMON_DETAIL_OBSERVER_FACTORY', factory, raising=False)
    return store, dashboard, request, io, factory_calls, startup


def common_evidence(store, result):
    run = store.get_run(result['run']['run_id'])
    row = next(row for row in run['targets'] if row['target_label'] == 'miaoshou:COMMON')
    assert row['status'] == 'SUCCEEDED'
    evidence = row['readback']['evidence']
    with sqlite3.connect(store.path) as db:
        raw, digest = db.execute(
            'SELECT evidence_json,evidence_digest FROM release_target_readbacks '
            'WHERE run_id=? AND target_label=?', (run['run_id'], 'miaoshou:COMMON')).fetchone()
    assert hashlib.sha256(raw.encode()).hexdigest() == digest
    assert json.loads(raw) == evidence
    return row, evidence


def assert_native_receipt(store, result, io, factory_calls):
    row, evidence = common_evidence(store, result)
    assert factory_calls, 'real COMMON call site did not consume service-owned observer factory'
    assert io.observer_reads, 'real COMMON call site only used the legacy parsed-dict transport'
    packet = evidence['native_common_observation']
    client.validate_common_observation_receipt(packet, detail_id=evidence['offer_id'])
    assert base64.b64decode(packet['wire_base64']) == io.responses[-1]
    assert base64.b64decode(packet['business_base64']) == io.responses[-1]
    comparison = {key: value for key, value in evidence.items()
                  if key not in {'native_common_observation', 'stored_common_lineage'}}
    expected = hashlib.sha256(json.dumps(comparison, ensure_ascii=False,
        sort_keys=True, separators=(',', ':')).encode('utf-8')).hexdigest()
    assert packet['comparison_sha256'] == expected
    lineage = evidence['stored_common_lineage']
    plan = store.get_plan(result['run']['plan_id'])
    assert lineage == {'schema_version': 'stored-common-observation-lineage/v1',
        'plan_id': plan['plan_id'], 'run_id': result['run']['run_id'],
        'target_label': 'miaoshou:COMMON', 'attempt': row['attempts'],
        'offer_id': plan['product_id'], 'plan_payload_digest': plan['payload_digest'],
        'comparison_sha256': expected, 'execution_authority': False}
    assert packet['execution_authority'] is False
    assert packet['evidence_kind'] == 'NATIVE_TRANSPORT_OBSERVATION_NOT_APPROVAL'
    encoded = json.dumps(evidence)
    assert CONFIG['app_id'] not in encoded and CONFIG['app_secret'] not in encoded
    return evidence


def test_service_common_write_keeps_wire_bytes_and_existing_lineage(tmp_path, monkeypatch):
    """Installed READ observer plus historical approval is not an EDIT grant."""
    store, dashboard, request, io, calls, startup = context(tmp_path, monkeypatch, authority=False)
    original = deepcopy(store.get_plan(request['plan_id']))
    status, blocked = server._prepare_miaoshou_release(dict(request, confirm_miaoshou_write=True))
    assert status == 409 and blocked['error'] == 'COMMON_TECHNICAL_ADMISSION_BLOCKED', blocked
    assert blocked['execution_authority'] is False and blocked['external_writes_performed'] == []
    assert io.edits == [] and io.reads == [] and io.observer_reads == [] and calls == []
    assert store.get_plan(request['plan_id']) == original
    with store._connect_readonly() as db:
        assert db.execute('SELECT COUNT(*) FROM release_runs').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM release_target_readbacks').fetchone()[0] == 0


def test_service_common_successor_reuse_keeps_new_receipt_without_rewriting_old(tmp_path, monkeypatch):
    """Historical normalized predecessor remains byte-stable, not native authority."""
    from owned_common_review_fixtures import historical_completed_context
    from shared_platform import publication_r3_image_bridge as bridge
    from shared_platform.publication_common_write_admission import inspect_common_write_admission
    _, dashboard, store, request, transport = historical_completed_context(tmp_path, monkeypatch)
    predecessor = deepcopy(store.get_plan(request['plan_id']))
    with store._connect_readonly() as db:
        retained = [tuple(r) for r in db.execute('SELECT * FROM release_target_readbacks')]
        old_approvals = [tuple(r) for r in db.execute('SELECT * FROM release_approvals')]
        before_approval_rows = {r['approval_id']: dict(r) for r in db.execute('SELECT * FROM release_approvals')}
        old_runs = [tuple(r) for r in db.execute('SELECT * FROM release_runs')]
        assert 'native_common_observation' not in json.loads(retained[0][2])
    store.supersede_plan(request['plan_id'], reason='owned legacy successor')
    # The fixture explicitly supersedes this historical plan. Verify exactly
    # that domain transition before defining the stable interface baseline.
    superseded = store.get_plan(request['plan_id'])
    assert superseded['payload'] == predecessor['payload']
    assert superseded['status'] == release_store.SUPERSEDED
    with store._connect_readonly() as db:
        after_approval_rows = {r['approval_id']: dict(r) for r in db.execute('SELECT * FROM release_approvals')}
        assert after_approval_rows.keys() == before_approval_rows.keys()
        changed = 0
        for approval_id, before_row in before_approval_rows.items():
            after_row = after_approval_rows[approval_id]
            if before_row['plan_id'] == request['plan_id'] and before_row['status'] == 'APPROVED':
                assert before_row['superseded_at'] is None
                assert after_row['status'] == 'SUPERSEDED'
                assert after_row['superseded_at'] == superseded['superseded_at'] and after_row['superseded_at']
                assert {k:v for k,v in after_row.items() if k not in {'status','superseded_at'}} == {
                    k:v for k,v in before_row.items() if k not in {'status','superseded_at'}}
                changed += 1
            else:
                assert after_row == before_row
        assert changed == 1
        assert [tuple(r) for r in db.execute('SELECT * FROM release_target_readbacks')] == retained
        assert [tuple(r) for r in db.execute('SELECT * FROM release_runs')] == old_runs
        old_approvals = [tuple(r) for r in db.execute('SELECT * FROM release_approvals')]
    def assert_retained_interface_baseline():
        with store._connect_readonly() as db:
            assert [tuple(r) for r in db.execute('SELECT * FROM release_approvals')] == old_approvals
            assert [tuple(r) for r in db.execute('SELECT * FROM release_target_readbacks')] == retained
            assert [tuple(r) for r in db.execute('SELECT * FROM release_runs')] == old_runs
        assert store.get_plan(request['plan_id'])['payload'] == predecessor['payload']
    # Keep the actual complete R1/R2 and exact COMMON scope. A generic old
    # marketplace dashboard lacks Ozon copy and never reaches this retired route.
    assert request['release_stage'] == 'R3_COMMON' and request['publication_targets'] == ['miaoshou:COMMON']
    code, retired = server._approve_release_plan_locally(
        dict(request, user_approved=True, approved_by='Kyle'))
    assert code == 409 and retired['error'] == 'R3_COMMON_LEGACY_APPROVAL_RETIRED', retired
    assert retired['external_writes_performed'] == []
    assert_retained_interface_baseline()
    # Existing Store fixture interface seeds only a new historical inert candidate.
    # Its content-package tag is not a native prepared receipt or an approval.
    payload = deepcopy(predecessor['payload'])
    payload['content_package_id'] += ':owned-historical-successor'
    payload['plan_id'] = bridge.common_stage_plan_id(payload, offer_id=payload['product_id'])
    created = store.create_plan(payload, supersedes_plan_id=request['plan_id'])
    assert_retained_interface_baseline()
    successor = deepcopy(store.get_plan(created['plan_id']))
    assert successor['plan_id'] != request['plan_id'] and successor['payload'] == payload
    assert successor['status'] == release_store.PLAN_PENDING_APPROVAL and successor.get('approval') is None
    assert not payload['r3_stage_binding'].get('native_preparation_source')
    held = inspect_common_write_admission(successor, store=store)
    assert_retained_interface_baseline()
    assert held['status'] == 'BLOCKED' and held['execution_authority'] is False
    assert held['final_review_available'] is False and held['external_writes_performed'] == []
    assert set(held['authority_facts'].values()) == {'UNKNOWN'}
    assert held['source_facts']['official_provenance'] == held['source_facts']['budget_status'] == 'UNKNOWN'
    assert held['source_facts']['execution_authority'] is False
    successor_request = {**request, 'plan_id':successor['plan_id'],
                         'confirmation_token':successor['confirmation_token']}
    status, blocked = server._prepare_miaoshou_release(dict(successor_request, confirm_miaoshou_write=True))
    assert_retained_interface_baseline()
    assert status == 409 and blocked['error'] == 'approved ReleasePlan no longer matches current facts', blocked
    assert blocked['external_writes_performed'] == [] and transport.mutations == 0
    assert store.get_plan(successor['plan_id']) == successor
    assert store.get_plan(request['plan_id'])['payload'] == predecessor['payload']
    with store._connect_readonly() as db:
        assert [tuple(r) for r in db.execute('SELECT * FROM release_target_readbacks')] == retained
        assert [tuple(r) for r in db.execute('SELECT * FROM release_approvals')] == old_approvals
        assert [tuple(r) for r in db.execute('SELECT * FROM release_runs')] == old_runs
        assert db.execute('SELECT COUNT(*) FROM release_runs').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM release_approvals WHERE plan_id=?',
                          (successor['plan_id'],)).fetchone()[0] == 0



def test_service_common_failed_write_reconciles_new_receipt_without_redispatch(tmp_path, monkeypatch):
    """Historical failure is retained; no observer invents native authority/acceptance."""
    store, dashboard, request, io, calls, startup = context(tmp_path, monkeypatch, authority=False)
    original = deepcopy(store.get_plan(request['plan_id']))
    run = store.start_run(request['plan_id'])
    store.begin_target(run['run_id'], 'miaoshou:COMMON')
    failure = {'source':'owned-historical-normalized-fixture','verified':False,
               'save_accepted':True,'write_outcome':'unknown_after_dispatch',
               'external_writes_performed':['miaoshou:COMMON:immutable_plan_write']}
    store.record_target_failure(run['run_id'], 'miaoshou:COMMON', error='owned historical failure', failure_evidence=failure)
    retained = deepcopy(store.get_run(run['run_id']))
    for _ in range(2):
        status, blocked = server._prepare_miaoshou_release(dict(request, confirm_miaoshou_write=True))
        assert status == 409 and blocked['error'] == 'COMMON_TECHNICAL_ADMISSION_BLOCKED', blocked
        assert blocked['execution_authority'] is False and blocked['external_writes_performed'] == []
        assert io.edits == [] and io.reads == [] and io.observer_reads == [] and calls == []
        assert store.get_plan(request['plan_id']) == original
        assert store.get_run(run['run_id']) == retained
    with store._connect_readonly() as db:
        assert json.loads(db.execute('SELECT evidence_json FROM release_target_failure_events').fetchone()[0]) == failure
        assert db.execute('SELECT COUNT(*) FROM release_target_readbacks').fetchone()[0] == 0
    assert 'native_common_observation' not in failure


def test_unknown_edit_acceptance_is_not_invented_by_observer(tmp_path, monkeypatch):
    """Historical failure is retained; no observer invents native authority/acceptance."""
    store, dashboard, request, io, calls, startup = context(tmp_path, monkeypatch, authority=False)
    original = deepcopy(store.get_plan(request['plan_id']))
    run = store.start_run(request['plan_id'])
    store.begin_target(run['run_id'], 'miaoshou:COMMON')
    failure = {'source':'owned-historical-normalized-fixture','verified':False,
               'save_accepted':False,'write_outcome':'unknown_after_dispatch',
               'external_writes_performed':['miaoshou:COMMON:immutable_plan_write']}
    store.record_target_failure(run['run_id'], 'miaoshou:COMMON', error='owned historical failure', failure_evidence=failure)
    retained = deepcopy(store.get_run(run['run_id']))
    for _ in range(2):
        status, blocked = server._prepare_miaoshou_release(dict(request, confirm_miaoshou_write=True))
        assert status == 409 and blocked['error'] == 'COMMON_TECHNICAL_ADMISSION_BLOCKED', blocked
        assert blocked['execution_authority'] is False and blocked['external_writes_performed'] == []
        assert io.edits == [] and io.reads == [] and io.observer_reads == [] and calls == []
        assert store.get_plan(request['plan_id']) == original
        assert store.get_run(run['run_id']) == retained
    with store._connect_readonly() as db:
        assert json.loads(db.execute('SELECT evidence_json FROM release_target_failure_events').fetchone()[0]) == failure
        assert db.execute('SELECT COUNT(*) FROM release_target_readbacks').fetchone()[0] == 0
    assert 'native_common_observation' not in failure


def test_service_observer_install_is_lazy_and_pinned_to_startup_root(tmp_path, monkeypatch):
    startup = publication_runtime_config.capture_startup_config(root=tmp_path, environ={})
    monkeypatch.setattr(server, '_COMMON_DETAIL_OBSERVER_FACTORY', None, raising=False)
    config_dir = tmp_path / 'config'
    config_dir.mkdir()
    (config_dir / 'miaoshou.local.json').write_text(json.dumps(CONFIG), encoding='utf-8')
    request_calls = []
    monkeypatch.setattr(client.urllib.request, 'urlopen',
                        lambda *args, **kw: request_calls.append(args) or pytest.fail('no transport during installation'))
    # The production installer is the service composition path, not an HTTP token.
    server._install_service_common_detail_observer(startup)
    assert request_calls == []
    observer = server._COMMON_DETAIL_OBSERVER_FACTORY()
    assert type(observer) is client.NativeCommonDetailObserver
    assert request_calls == []


def test_installed_observer_does_not_open_common_authority_or_final_review(tmp_path, monkeypatch):
    store, dashboard, request, io, calls, startup = context(tmp_path, monkeypatch, authority=False)
    status, blocked = server._prepare_miaoshou_release(dict(request, confirm_miaoshou_write=True))
    assert status == 409 and blocked['error'] == 'COMMON_TECHNICAL_ADMISSION_BLOCKED'
    assert io.edits == [] and io.reads == [] and calls == []
    plan = store.get_plan(request['plan_id'])
    diagnostic = publication_common_write_admission.inspect_common_write_admission(plan)
    assert diagnostic['status'] == 'BLOCKED'
    assert 'COMMON_HISTORICAL_WRITE_BUDGET_UNKNOWN' in diagnostic['blockers']
