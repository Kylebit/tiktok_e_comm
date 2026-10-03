"""Real service/config/store producers with a CLOSED test transport.

Retained success is local context, not a provider permission or native grant.
No test calls a provider or opens an HTTP approval/dispatch route.
"""
import json

import pytest

from shared_platform.native_common_signing_context import NativeCommonSigningContextReader, inspect_service_signing_context
from shared_platform.publication_runtime_config import StartupConfig
from shared_platform.release_store import ReleaseStore
from test_round1_workspace_freeze import live
from test_native_common_technical_execution import _setup
from test_native_common_retained_review_graph import _completed
from test_common_detail_observation import CONFIG


def _config(tmp_path):
    root = tmp_path/'signing-config'
    (root/'config').mkdir(parents=True)
    (root/'config/miaoshou.local.json').write_text(json.dumps(CONFIG), encoding='utf-8')
    return StartupConfig(root, 'unused-policy', 'unused-incidents', True, True)


def test_same_native_store_pinned_signing_context_is_distinct_from_r1_account(live, monkeypatch, tmp_path):
    store, plan, payload, facts = _setup(live, monkeypatch, tmp_path)
    config = _config(tmp_path)
    from modules.products import server
    monkeypatch.setattr(server, '_COMMON_SIGNING_CONTEXT_CONFIG', None)
    assert server._service_common_signing_context_reader(store) is None
    assert server._install_service_common_signing_context(config) is config
    reader = server._service_common_signing_context_reader(store)
    before = store.path.read_bytes()
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        observed = inspect_service_signing_context(reader, db, plan['plan_id'])
    value = observed.diagnostic()
    assert value['status'] == 'PINNED_COMMON_SIGNING_CONTEXT', value
    assert value['r1_source_account_digest'] == payload['r3_stage_binding']['native_preparation_source']['account_identity_digest']
    assert value['common_signing_context_digest'] != value['r1_source_account_digest']
    assert value['write_endpoint_permission'] == value['current_read_acceptance'] == value['provider_account_number'] == 'UNKNOWN'
    assert value['execution_authority'] is False and store.path.read_bytes() == before
    assert CONFIG['app_id'] not in json.dumps(value) and CONFIG['app_secret'] not in json.dumps(value)


def test_closed_retained_read_context_succeeds_but_changed_app_is_unknown(live, monkeypatch, tmp_path):
    store, plan, payload, facts, run_id = _completed(live, monkeypatch, tmp_path)
    config = _config(tmp_path)
    reader = NativeCommonSigningContextReader(config, store)
    before = store.path.read_bytes()
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        value = reader.read(db, plan['plan_id'], common_run_id=run_id).diagnostic()
    assert value['status'] == 'PINNED_APP_AND_RETAINED_COMMON_READ_CONTEXT', value
    assert value['retained_readback_digest'] == store.get_run(run_id)['targets'][0]['readback']['evidence_digest']
    assert value['current_read_acceptance'] == value['write_endpoint_permission'] == 'UNKNOWN'
    assert value['execution_authority'] is False
    changed = dict(CONFIG, app_id=CONFIG['app_id']+'-changed')
    (config.root/'config/miaoshou.local.json').write_text(json.dumps(changed), encoding='utf-8')
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        rejected = reader.read(db, plan['plan_id'], common_run_id=run_id)
    assert rejected.status == 'UNKNOWN' and rejected.reason == 'COMMON_SIGNING_RETAINED_CONTEXT_CHANGED'
    assert store.path.read_bytes() == before


def test_explicit_current_signed_read_has_real_comparison_but_no_edit_grant(live, monkeypatch, tmp_path):
    from test_common_detail_observation import LocalResponse, install_response
    from test_service_common_observation import ClosedCommonTransport
    store, plan, payload, facts = _setup(live, monkeypatch, tmp_path)
    detail = ClosedCommonTransport.detail_for(payload)
    wire = json.dumps({'result':'success', 'data':{'editCommonCollectBoxDetail':detail}}, ensure_ascii=False).encode()
    calls = install_response(monkeypatch, LocalResponse(wire))
    reader = NativeCommonSigningContextReader(_config(tmp_path), store)
    before = store.path.read_bytes()
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        current = reader.read_current(db, plan['plan_id'])
    value = current.diagnostic()
    assert value['status'] == 'CURRENT_SIGNED_COMMON_READ_ACCEPTED' and value['current_read_acceptance'] == 'ACCEPTED', value
    assert value['current_fields_match'] is True and current.current_observation_bytes
    assert calls == [{'commonCollectBoxDetailId':int(payload['product_id'])}]
    assert value['write_endpoint_permission'] == 'UNKNOWN' and value['execution_authority'] is False
    assert store.path.read_bytes() == before and 'current_observation_bytes' not in value


def test_current_endpoint_permission_denial_never_reuses_retained_acceptance(live, monkeypatch, tmp_path):
    from test_common_detail_observation import LocalResponse, install_response
    store, plan, payload, facts, run_id = _completed(live, monkeypatch, tmp_path)
    calls = install_response(monkeypatch, LocalResponse(b'{"result":"fail","code":"appNoPermission","message":"denied","data":null}'))
    reader = NativeCommonSigningContextReader(_config(tmp_path), store)
    before = store.path.read_bytes()
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        rejected = reader.read_current(db, plan['plan_id'], common_run_id=run_id)
    assert rejected.status == 'UNKNOWN' and rejected.current_read_acceptance == 'REJECTED'
    assert rejected.reason == 'COMMON_SIGNING_CURRENT_READ_REJECTED:appNoPermission'
    assert rejected.current_observation_bytes is None and rejected.diagnostic()['execution_authority'] is False
    assert len(calls) == 1 and store.path.read_bytes() == before


def test_missing_database_config_and_caller_receipt_create_nothing(live, monkeypatch, tmp_path):
    store, plan, payload, facts = _setup(live, monkeypatch, tmp_path)
    root = tmp_path/'missing-config'
    config = StartupConfig(root, 'unused', 'unused', True, True)
    before = store.path.read_bytes()
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        value = inspect_service_signing_context(NativeCommonSigningContextReader(config, store), db, plan['plan_id'])
        assert value.status == 'UNKNOWN' and not root.exists()
        forged = inspect_service_signing_context({'approved': True, 'credential_scope_digest':'owned'}, db, plan['plan_id'])
        assert forged.reason == 'COMMON_SIGNING_SERVICE_READER_REQUIRED'
        missing = ReleaseStore(tmp_path/'never-created.db')
        rejected = NativeCommonSigningContextReader(_config(tmp_path), missing).read(db, plan['plan_id'])
        assert rejected.reason == 'COMMON_SIGNING_STORE_UNAVAILABLE' and not missing.path.exists()
    assert store.path.read_bytes() == before


@pytest.mark.parametrize('raw', ['null', '{', '{"app_id":"x","app_id":"y"}', '{"app_id":"x","app_secret":"s","base_url":null}', '{"app_id":"x","app_secret":"s","base_url":"https://foreign.invalid"}'],
    ids=['null', 'bad-json', 'duplicate', 'null-base', 'foreign-base'])
def test_invalid_native_signing_config_remains_unknown(live, monkeypatch, tmp_path, raw):
    store, plan, payload, facts = _setup(live, monkeypatch, tmp_path)
    config = _config(tmp_path)
    (config.root/'config/miaoshou.local.json').write_text(raw, encoding='utf-8')
    before = store.path.read_bytes()
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        assert NativeCommonSigningContextReader(config, store).read(db, plan['plan_id']).status == 'UNKNOWN'
    assert store.path.read_bytes() == before
