"""Real service configuration/registration with CLOSED signed transport.

No permission flag, private transaction facts or caller proof opens the service
producer. Owned schema installation is explicit; provider acceptance is fake
transport evidence here, never a production grant or migration.
"""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from hashlib import sha256
import json
from types import SimpleNamespace

import pytest

from modules.products import server, release_adapters
from modules.miaoshou import client
from shared_platform import native_common_technical_execution as technical
from shared_platform import native_common_edit_boundary as boundary
from shared_platform import release_store
from test_round1_workspace_freeze import live
from test_native_common_technical_execution import _setup, _successor
from test_native_common_baseline_source import _service_config
from test_common_detail_observation import CONFIG, LocalResponse
from test_service_common_observation import ClosedCommonTransport

# Capture the real signed client before the source-only chain installs its
# no-provider sentinel. Restore it only inside the CLOSED service seam below;
# urllib remains intercepted and every signature/endpoint is checked.
_REAL_POST_OPEN = client.post_open


def _installed(monkeypatch, tmp_path, store):
    config = _service_config(tmp_path, monkeypatch)
    monkeypatch.setattr(server, '_COMMON_STANDING_POLICY_READER', None)
    monkeypatch.setattr(server, '_COMMON_DETAIL_OBSERVER_FACTORY', None)
    monkeypatch.setattr(boundary, '_ACTIVE', None)
    server._install_service_common_standing_policy(config)
    server._install_service_common_detail_observer(config)
    _, active = boundary.install_service_boundary(config)
    monkeypatch.setattr(release_store, 'default_release_store', lambda: store)
    return config, active


def _closed_signed_transport(monkeypatch, store, payload, *, fail_edit=False, config=CONFIG):
    monkeypatch.setattr(client, 'post_open', _REAL_POST_OPEN)
    calls = []
    detail = ClosedCommonTransport.detail_for(payload)
    def closed(request, timeout):
        assert request.full_url.startswith(client.OPEN_BASE_URL)
        path = request.full_url[len(client.OPEN_BASE_URL):]
        assert path in {client.COMMON_DETAIL_OBSERVATION_PATH, boundary.EDIT_PATH}
        assert request.get_header('X-app-key') == config['app_id']
        raw = request.data.decode()
        timestamp = int(request.get_header('X-timestamp'))
        assert request.get_header('X-sign') == client._open_sign(config['app_secret'], path, timestamp, config['app_id'], raw)
        assert timeout == 30
        calls.append(path)
        if path == boundary.EDIT_PATH:
            # Observe the durable claim through another owned readonly handle
            # only after the producer's transaction has committed.
            with store._connect_readonly() as db:
                row = db.execute('SELECT * FROM release_runs').fetchone()
                assert row['approval_id'] is None and row['technical_execution_state'] == 'UNKNOWN'
                assert db.execute('SELECT COUNT(*) FROM release_approvals').fetchone()[0] == 0
            if fail_edit:
                raise OSError('closed unknown after dispatch')
            value = {'result':'success','data':{}}
        else:
            value = {'result':'success','data':{'editCommonCollectBoxDetail':deepcopy(detail),'ossMd5':'owned-current-md5'}}
        return LocalResponse(json.dumps(value, ensure_ascii=False).encode(), url=request.full_url)
    monkeypatch.setattr(client.urllib.request, 'urlopen', closed)
    monkeypatch.setattr(client.urllib.request, 'build_opener', lambda *handlers:SimpleNamespace(open=closed))
    monkeypatch.setattr(client, '_wait_for_open_slot', lambda:None)
    monkeypatch.setattr(client, '_load_config', lambda:pytest.fail('No ambient signing configuration'))
    return calls


def test_real_service_same_snapshot_facts_use_no_second_connection(live, monkeypatch, tmp_path):
    store, plan, payload, _ = _setup(live, monkeypatch, tmp_path)
    _, active = _installed(monkeypatch, tmp_path, store)
    before = store.path.read_bytes()
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        monkeypatch.setattr(store, '_connect_readonly', lambda:pytest.fail('Same snapshot, no second connection'))
        monkeypatch.setattr(store, '_connect', lambda:pytest.fail('No second connection or constructor writes'))
        coverage = active.same_snapshot_coverage(store, db, plan['plan_id'])
        facts = technical._service_facts(store, db, plan['plan_id'])
    assert facts.root_id == coverage.preparation_root_id
    assert facts.coverage_digest == sha256(technical._bytes(coverage.diagnostic())).hexdigest()
    assert coverage.diagnostic()['provider_edit_permission'] == 'UNKNOWN'
    assert coverage.diagnostic()['external_offer_history'] == 'NOT_ASSERTED'
    assert facts.confirmed_cap == 1 and facts.plan_id == plan['plan_id']
    assert store.path.read_bytes() == before


def test_real_server_native_request_pins_signing_and_closes_original_attempt(live, monkeypatch, tmp_path):
    store, plan, payload, _ = _setup(live, monkeypatch, tmp_path)
    _installed(monkeypatch, tmp_path, store)
    calls = _closed_signed_transport(monkeypatch, store, payload)
    code, value = server._prepare_miaoshou_release({'offer_id':payload['product_id'],
        'plan_id':plan['plan_id'],'confirm_miaoshou_write':True})
    assert code == 200 and value['native_run']['state'] == 'CONFIRMED_WRITE', value
    assert calls.count(boundary.EDIT_PATH) == 1
    run = store.get_run(value['native_run']['run_id'])
    assert run['targets'][0]['attempts'] == 1
    with store._connect_readonly() as db:
        stored = db.execute('SELECT evidence_json FROM release_target_readbacks WHERE run_id=?',
                            (value['native_run']['run_id'],)).fetchone()
    packet = json.loads(stored['evidence_json'])['native_common_observation']
    assert packet['credential_scope_digest'] == sha256(('miaoshou-app-scope:'+CONFIG['app_id']).encode()).hexdigest()
    assert packet['execution_authority'] is False
    assert store.get_plan(plan['plan_id'])['status'] == release_store.PLAN_PENDING_APPROVAL
    with store._connect_readonly() as db:
        assert db.execute('SELECT COUNT(*) FROM release_runs').fetchone()[0] == 1
        assert db.execute('SELECT COUNT(*) FROM release_approvals').fetchone()[0] == 0


def test_same_baseline_cap_is_not_reset_by_successor_and_readonly_reuse(live, monkeypatch, tmp_path):
    store, plan, payload, _ = _setup(live, monkeypatch, tmp_path)
    _installed(monkeypatch, tmp_path, store)
    calls = _closed_signed_transport(monkeypatch, store, payload)
    request = {'offer_id':payload['product_id'],'plan_id':plan['plan_id'],'confirm_miaoshou_write':True}
    code, value = server._prepare_miaoshou_release(request)
    assert code == 200 and value['native_run']['state'] == 'CONFIRMED_WRITE', value
    successor, next_payload, _ = _successor(store, payload, 'real-service-read-continuation')
    code, refusal = server._prepare_miaoshou_release({**request,'plan_id':successor['plan_id']})
    assert code == 409 and refusal['error'] == 'COMMON_CONFIRMED_WRITE_CAP_EXHAUSTED', refusal
    code, reused = server._prepare_miaoshou_release({'offer_id':payload['product_id'],
        'plan_id':successor['plan_id'],'reuse_miaoshou_readback':True})
    assert code == 200 and reused['native_run']['state'] == 'READONLY_REUSE', reused
    assert calls.count(boundary.EDIT_PATH) == 1
    assert reused['external_writes_performed'] == []
    with store._connect_readonly() as db:
        assert db.execute('SELECT COUNT(*) FROM release_approvals').fetchone()[0] == 0
        assert db.execute('SELECT COUNT(*) FROM release_runs').fetchone()[0] == 2


def test_two_real_managed_transports_dispatch_only_one_edit(live, monkeypatch, tmp_path):
    store, plan, payload, _ = _setup(live, monkeypatch, tmp_path)
    _, active = _installed(monkeypatch, tmp_path, store)
    calls = _closed_signed_transport(monkeypatch, store, payload)
    detail = ClosedCommonTransport.detail_for(payload)
    body = {'commonCollectBoxDetailId':int(payload['product_id']),
        'editCommonCollectBoxDetail':release_adapters._build_immutable_common_edit(payload, detail),
        'ossMd5':'owned-current-md5'}
    def send(_):
        post = active.bind_post(store, plan['plan_id'], client.post_open)
        post(client.COMMON_DETAIL_OBSERVATION_PATH, {'commonCollectBoxDetailId':int(payload['product_id'])})
        try:return post(boundary.EDIT_PATH, body)['result']
        except boundary.CommonEditBlocked:return 'blocked'
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(send, [0,1]))
    assert sorted(results) == ['blocked','success']
    assert calls.count(boundary.EDIT_PATH) == 1
    with store._connect_readonly() as db:
        runs = db.execute('SELECT * FROM release_runs').fetchall()
        assert len(runs) == 1 and runs[0]['technical_execution_state'] == 'UNKNOWN'
        assert db.execute('SELECT attempts FROM release_target_runs').fetchone()[0] == 1


def test_original_native_claim_rejects_read_from_another_signing_context(live, monkeypatch, tmp_path):
    store, plan, payload, _ = _setup(live, monkeypatch, tmp_path)
    _, active = _installed(monkeypatch, tmp_path, store)
    _closed_signed_transport(monkeypatch, store, payload)
    post = active.bind_post(store, plan['plan_id'], client.post_open)
    current = post(client.COMMON_DETAIL_OBSERVATION_PATH, {'commonCollectBoxDetailId':int(payload['product_id'])})
    body = {'commonCollectBoxDetailId':int(payload['product_id']),
        'editCommonCollectBoxDetail':release_adapters._build_immutable_common_edit(payload,
            current['data']['editCommonCollectBoxDetail']), 'ossMd5':current['data']['ossMd5']}
    post(boundary.EDIT_PATH, body)
    run_id, attempt = post._native_claim
    other = {**CONFIG,'app_id':CONFIG['app_id']+'-other'}
    _closed_signed_transport(monkeypatch, store, payload, config=other)
    readback = release_adapters.readback_miaoshou_common(payload,
        observation_reader=client.NativeCommonDetailObserver(other))
    evidence = release_adapters.bind_native_common_readback(readback, {**readback,
        'source':'miaoshou_open_api','external_writes_performed':['miaoshou:COMMON:immutable_plan_write']})
    before = store.path.read_bytes()
    with pytest.raises(release_store.ReleaseAuthorizationError, match='READBACK_SIGNING_CONTEXT_CHANGED'):
        technical.retain_readback(store, plan['plan_id'], run_id, attempt, evidence)
    assert store.path.read_bytes() == before
    assert technical.inspect_existing(store, run_id)['state'] == 'UNKNOWN'
    with store._connect_readonly() as db:
        assert db.execute('SELECT COUNT(*) FROM release_target_readbacks').fetchone()[0] == 0


def test_transport_unknown_restart_does_not_send_again(live, monkeypatch, tmp_path):
    store, plan, payload, _ = _setup(live, monkeypatch, tmp_path)
    _installed(monkeypatch, tmp_path, store)
    calls = _closed_signed_transport(monkeypatch, store, payload, fail_edit=True)
    request = {'offer_id':payload['product_id'],'plan_id':plan['plan_id'],'confirm_miaoshou_write':True}
    code, first = server._prepare_miaoshou_release(request)
    assert code == 502 and first['state'] == 'UNKNOWN', first
    old_calls = list(calls)
    code, again = server._prepare_miaoshou_release(request)
    assert code == 409 and again['error'] == 'COMMON_PRIOR_ATTEMPT_RECONCILIATION_REQUIRED'
    assert calls == old_calls and calls.count(boundary.EDIT_PATH) == 1
    assert again['native_run']['run_id'] == first['native_run_id']


@pytest.mark.parametrize('damage', ['uninstalled','foreign-startup','missing-config','bad-config','wrong-base','missing-schema','wrong-store'])
def test_missing_actual_service_facts_never_reserve_or_call(live, monkeypatch, tmp_path, damage):
    store, plan, payload, _ = _setup(live, monkeypatch, tmp_path)
    config, active = _installed(monkeypatch, tmp_path, store)
    path = config.root/'config/miaoshou.local.json'
    if damage == 'uninstalled':monkeypatch.setattr(boundary, '_ACTIVE', None)
    elif damage == 'foreign-startup':server._install_service_common_signing_context(_service_config(tmp_path/'other',monkeypatch))
    elif damage == 'missing-config':path.unlink()
    elif damage == 'bad-config':path.write_text('{bad json', encoding='utf-8')
    elif damage == 'wrong-base':path.write_text(json.dumps({**CONFIG,'base_url':'https://foreign.invalid'}),encoding='utf-8')
    elif damage == 'missing-schema':
        with technical._existing_transaction(store) as db:
            db.execute('DROP TRIGGER release_runs_native_common_scope')
    else:
        with store._connect_readonly() as db:
            db.execute('BEGIN')
            other = release_store.ReleaseStore(tmp_path/'absent.sqlite3')
            with pytest.raises(boundary.CommonEditBlocked, match='BOUNDARY_NOT_INSTALLED'):
                active.same_snapshot_coverage(other, db, plan['plan_id'])
            assert not other.path.exists()
        return
    before = store.path.read_bytes()
    monkeypatch.setattr(server, '_service_common_detail_observer', lambda:pytest.fail('Admission before transport'))
    code, value = server._prepare_miaoshou_release({'offer_id':payload['product_id'],
        'plan_id':plan['plan_id'],'confirm_miaoshou_write':True})
    assert code == 409 and value['execution_authority'] is False, value
    assert store.path.read_bytes() == before


def test_config_change_and_caller_override_fail_before_claim(live, monkeypatch, tmp_path):
    store, plan, payload, _ = _setup(live, monkeypatch, tmp_path)
    config, active = _installed(monkeypatch, tmp_path, store)
    calls = _closed_signed_transport(monkeypatch, store, payload)
    post = active.bind_post(store, plan['plan_id'], client.post_open)
    with pytest.raises(boundary.CommonEditBlocked, match='CALLER_SIGNING_OVERRIDE'):
        post(client.COMMON_DETAIL_OBSERVATION_PATH, {}, cfg=CONFIG)
    post(client.COMMON_DETAIL_OBSERVATION_PATH, {'commonCollectBoxDetailId':int(payload['product_id'])})
    (config.root/'config/miaoshou.local.json').write_text(json.dumps({**CONFIG,'app_secret':'changed'}),encoding='utf-8')
    with pytest.raises(boundary.CommonEditBlocked, match='PINNED_CONFIG_CHANGED'):
        post(boundary.EDIT_PATH, {})
    assert boundary.EDIT_PATH not in calls
    with store._connect_readonly() as db:
        assert db.execute('SELECT COUNT(*) FROM release_runs').fetchone()[0] == 0
