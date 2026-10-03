"""Actual local entry refusals; no provider permission is synthesized here."""
import http.client
import json
from copy import deepcopy

import pytest

from modules.miaoshou import client, oneclick_release
from modules.products import release_adapters
from modules.sourcing import new_product_workbench as workbench
from shared_platform import native_common_edit_boundary as boundary
from shared_platform import release_store
from shared_platform.publication_runtime_config import capture_startup_config
from test_round1_workspace_freeze import live
from test_native_common_technical_execution import _setup
from test_service_common_observation import ClosedCommonTransport


@pytest.mark.parametrize('name', [
    'sync_generated_image_to_miaoshou', '_write_ordered_images_to_miaoshou_unlocked',
    'write_approved_generated_images_to_miaoshou', 'write_miaoshou_draft',
    'ensure_common_sequential_skus',
])
def test_legacy_python_common_edit_cannot_borrow_an_injected_transport(monkeypatch, name):
    calls = []
    def post(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError('No legacy write or preliminary read may be sent')
    args = ('3828811808', 'asset', 'keep') if name == 'sync_generated_image_to_miaoshou' else ('3828811808',)
    with pytest.raises(boundary.CommonEditBlocked, match='PERSISTED_NATIVE_BASELINE_REQUIRED'):
        getattr(workbench, name)(*args, post=post)
    assert calls == []


def test_adapter_and_oneclick_occurrence_are_not_native_ledger_authority(monkeypatch):
    calls = []
    def post(*args, **kwargs):
        calls.append(args)
        raise AssertionError('No unclaimed COMMON request')
    with pytest.raises(boundary.CommonEditBlocked, match='PERSISTED_NATIVE_BASELINE_REQUIRED'):
        release_adapters.write_miaoshou_common_from_plan({}, post=post)
    monkeypatch.setattr(oneclick_release, '_runtime_transport', lambda: object())
    monkeypatch.setattr(oneclick_release, '_required_post', lambda _: post)
    monkeypatch.setattr(oneclick_release, '_open_write', lambda *a, **k: pytest.fail('No occurrence may masquerade as a claim'))
    with pytest.raises(boundary.CommonEditBlocked, match='PERSISTED_NATIVE_BASELINE_REQUIRED'):
        oneclick_release._dispatch_common(object(), {})
    assert calls == []


def test_exact_edit_client_gate_precedes_config_signing_and_network(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('Unclaimed EDIT must fail before credentials, signing or network')
    monkeypatch.setattr(client, '_load_config', forbidden)
    monkeypatch.setattr(client, '_wait_for_open_slot', forbidden)
    monkeypatch.setattr(client.urllib.request, 'urlopen', forbidden)
    with pytest.raises(boundary.CommonEditBlocked, match='SAME_LEDGER_CLAIM_REQUIRED'):
        client.post_open(boundary.EDIT_PATH, {'commonCollectBoxDetailId': 3828811808})
    # The gate affects this one endpoint; a read/cost/catalog path is not a
    # COMMON mutation and does not acquire a false native-write prerequisite.
    boundary.require_edit_dispatch('/open/v1/unrelated/cost_update', {})
    boundary.require_edit_dispatch(client.COMMON_DETAIL_OBSERVATION_PATH, {})


@pytest.mark.parametrize('path', [
    '/api/new-product/miaoshou-draft/commit', '/api/new-product/sku-numbering/fix',
])
def test_real_legacy_http_route_rejects_caller_identity_before_workbench(live, monkeypatch, path):
    calls = []
    def forbidden(*args, **kwargs):
        calls.append((args, kwargs))
        pytest.fail('Caller-supplied plan/root/approval cannot enter the old writer')
    monkeypatch.setattr(workbench, 'write_miaoshou_draft', forbidden)
    monkeypatch.setattr(workbench, 'ensure_common_sequential_skus', forbidden)
    connection = http.client.HTTPConnection('127.0.0.1', live['port'], timeout=10)
    try:
        connection.request('POST', path, json.dumps({'offer_id': live['offer'],
            'plan_id': 'caller-plan', 'prepared_root': 'caller-root',
            'coverage': 'complete', 'approved': True}), {'Content-Type': 'application/json'})
        response = connection.getresponse()
        code, value = response.status, json.loads(response.read())
    finally:
        connection.close()
    # The actual public Handler retires these paths before its legacy writer
    # branches. Do not revive the retired route just to reach a lower gate.
    assert code == 410
    assert value == {'ok': False, 'error': 'Orbit Treasury moved to http://127.0.0.1:8766/'}
    assert calls == []


def test_registered_boundary_with_missing_db_never_creates_or_dispatches(tmp_path):
    config = capture_startup_config(root=tmp_path, environ={})
    previous, installed = boundary.install_service_boundary(config)
    missing = release_store.ReleaseStore(tmp_path/'missing.sqlite3')
    calls = []
    try:
        facts = installed.installation_facts(missing).diagnostic()
        assert facts['registration_status'] == 'NOT_INSTALLED'
        assert facts['schema_status'] == 'UNKNOWN' and not facts['execution_authority']
        with pytest.raises(boundary.CommonEditBlocked, match='NATIVE_PLAN_AND_STORE_REQUIRED'):
            installed.bind_post(missing, 'caller-plan', lambda *a: calls.append(a))
        assert not missing.path.exists() and calls == []
    finally:
        boundary.restore_service_boundary(previous, installed)


def test_true_persisted_root_and_installed_schema_are_not_an_account_write_grant(live, monkeypatch, tmp_path):
    store, plan, payload, fixture_facts = _setup(live, monkeypatch, tmp_path)
    previous, installed = boundary.install_service_boundary(capture_startup_config(root=tmp_path, environ={}))
    calls = []
    before = store.path.read_bytes()
    try:
        facts = installed.installation_facts(store, plan['plan_id']).diagnostic()
        assert facts['registration_status'] == 'REGISTERED_NAMED_BOUNDARIES'
        assert facts['schema_status'] == 'INSTALLED'
        assert facts['preparation_root_id'] == fixture_facts.root_id
        assert facts['mutation_digest'] == fixture_facts.binding()['mutation_sha256']
        assert facts['provider_edit_permission'] == 'UNKNOWN' and not facts['execution_authority']
        transport = installed.bind_post(store, plan['plan_id'], lambda *a: calls.append(a))
        with pytest.raises(boundary.CommonEditBlocked, match='^COMMON_EDIT_SERVICE_SIGNING_CONTEXT_REQUIRED$'):
            transport(boundary.EDIT_PATH, {})
        assert calls == [] and store.path.read_bytes() == before
        with store._connect_readonly() as db:
            assert db.execute('SELECT COUNT(*) FROM release_runs').fetchone()[0] == 0
            assert db.execute('SELECT COUNT(*) FROM release_approvals').fetchone()[0] == 0
        with pytest.raises(TypeError):
            installed.bind_post(store, plan['plan_id'], lambda *a: None, facts=fixture_facts)
    finally:
        boundary.restore_service_boundary(previous, installed)


@pytest.mark.parametrize('change', ['unrelated-field', 'sku-price', 'md5', 'foreign-offer'])
def test_exact_envelope_comparison_preserves_nonmutation_provider_fields(live, monkeypatch, tmp_path, change):
    # Real preparation rows and original immutable adapter builder; this only
    # tests body comparison. No private facts enter the service dispatch path.
    store, plan, payload, facts = _setup(live, monkeypatch, tmp_path)
    current = ClosedCommonTransport.detail_for(payload)
    current['untouchedProviderField'] = 'kept'
    for sku in current['skuMap'].values():
        sku['price'] = 9.5
    response = {'result': 'success', 'data': {'editCommonCollectBoxDetail': current, 'ossMd5': 'owned-md5'}}
    body = {'commonCollectBoxDetailId': int(payload['product_id']), 'ossMd5': 'owned-md5',
            'editCommonCollectBoxDetail': release_adapters._build_immutable_common_edit(payload, current)}
    with store._connect_readonly() as db:
        db.execute('BEGIN')
        boundary.NativeCommonEditBoundary._check_mutation(db, facts, body, response)
        changed = deepcopy(body)
        if change == 'unrelated-field':
            changed['editCommonCollectBoxDetail']['untouchedProviderField'] = 'changed'
        elif change == 'sku-price':
            next(iter(changed['editCommonCollectBoxDetail']['skuMap'].values()))['price'] = 0
        elif change == 'md5':
            changed['ossMd5'] = 'other'
        else:
            changed['commonCollectBoxDetailId'] += 1
        with pytest.raises(boundary.CommonEditBlocked, match='COMMON_EDIT_(MUTATION_CHANGED|EXACT_MUTATION_REQUIRED)'):
            boundary.NativeCommonEditBoundary._check_mutation(db, facts, changed, response)
