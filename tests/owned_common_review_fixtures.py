"""Owned local fixtures, with native completion and historical data kept apart.

Only the native branch dispatches through the real managed service and CLOSED
signed urllib transport. Historical normalized records are explicitly seeds of
past private ledger mechanics; they make no native/account/budget authority.
"""
from contextlib import contextmanager
from copy import deepcopy
import json

from modules.products import server, release_adapters
from modules.miaoshou import client

# Collection-time real function, before native source fixtures deny provider I/O.
_REAL_POST_OPEN = client.post_open


class ObservedClosedTransport:
    def __init__(self, calls, detail):
        self.calls, self.detail = calls, detail

    @property
    def mutations(self):
        from shared_platform.native_common_edit_boundary import EDIT_PATH
        return self.calls.count(EDIT_PATH)

    @property
    def reads(self):
        return self.calls.count(client.COMMON_DETAIL_OBSERVATION_PATH)


@contextmanager
def native_ready_context(tmp_path, monkeypatch, *, pretty_wire=False, fail_edit=False, damage_readback=False):
    """Actual native source/config/schema; only transport is closed in owned tests."""
    from types import SimpleNamespace
    from test_round1_workspace_freeze import live
    from test_native_common_retained_review_graph import _propose_market_inputs
    from test_native_common_technical_execution import _setup
    from test_native_common_service_facts import _installed, _closed_signed_transport
    from test_common_detail_observation import LocalResponse
    from test_service_common_observation import ClosedCommonTransport
    from shared_platform.native_common_edit_boundary import EDIT_PATH
    from test_round1_auto_freeze import public_settings
    # The producer helper is called directly, so its module's autouse fixture
    # is not registered for this caller. Pin the same public FX inputs explicitly.
    public_settings.__wrapped__(monkeypatch)
    root = tmp_path/'native-completed-source'
    root.mkdir()
    with contextmanager(live.__wrapped__)(root, monkeypatch) as owned:
        # Keep the actual live fixture's complete scope, including its MY
        # Shopee category source, before the original context/capture/freeze.
        _propose_market_inputs(owned, monkeypatch)
        store, plan, payload, _ = _setup(owned, monkeypatch, root)
        config, _ = _installed(monkeypatch, root, store)
        monkeypatch.setattr(client, 'post_open', _REAL_POST_OPEN)
        calls = _closed_signed_transport(monkeypatch, store, payload, fail_edit=fail_edit)
        original_open = client.urllib.request.urlopen
        responses, observer_calls, observer_reads = [], [], []
        original_factory = server._COMMON_DETAIL_OBSERVER_FACTORY
        def factory():
            observer_calls.append('service-owned-read-only')
            return original_factory()
        monkeypatch.setattr(server, '_COMMON_DETAIL_OBSERVER_FACTORY', factory)
        def closed_open(request, timeout):
            response = original_open(request, timeout)  # Exact original signature/UNKNOWN checks.
            if request.full_url == client.OPEN_BASE_URL + client.COMMON_DETAIL_OBSERVATION_PATH:
                value = json.loads(response.read())
                if damage_readback and EDIT_PATH in calls:
                    value['data']['editCommonCollectBoxDetail']['title'] = 'closed stale provider title'
                raw = json.dumps(value, ensure_ascii=False, indent=2 if pretty_wire else None).encode('utf-8')
                responses.append(raw)
                return LocalResponse(raw, url=request.full_url)
            return response
        monkeypatch.setattr(client.urllib.request, 'urlopen', closed_open)
        def observe_open(request, timeout):
            assert request.full_url in {
                client.OPEN_BASE_URL + client.COMMON_DETAIL_OBSERVATION_PATH,
                client.OPEN_BASE_URL + EDIT_PATH}
            if request.full_url == client.OPEN_BASE_URL + client.COMMON_DETAIL_OBSERVATION_PATH:
                observer_reads.append(json.loads(request.data))
            return closed_open(request, timeout)
        monkeypatch.setattr(client.urllib.request, 'build_opener', lambda *handlers:SimpleNamespace(open=observe_open))
        io = ObservedClosedTransport(calls, ClosedCommonTransport.detail_for(payload))
        io.responses, io.observer_reads, io.factory_calls = responses, observer_reads, observer_calls
        request = {'offer_id':payload['product_id'], 'plan_id':plan['plan_id'], 'confirm_miaoshou_write':True}
        yield owned, store, plan, payload, config, request, io


def native_completed_context(tmp_path, monkeypatch, *, pretty_wire=False):
    from shared_platform import publication_r3_image_bridge as bridge, release_control
    from shared_platform.native_common_baseline_source import NativeCommonBaselineReader
    from shared_platform.release_store import PLAN_PENDING_APPROVAL
    with native_ready_context(tmp_path, monkeypatch, pretty_wire=pretty_wire) as (_, store, plan, payload, config, request, io):
        code, result = server._prepare_miaoshou_release(request)
        assert code == 200 and result['native_run']['state'] == 'CONFIRMED_WRITE', result
        run = store.get_run(result['native_run']['run_id'])
        assert run['approval_id'] is None and run['targets'][0]['attempts'] == 1
        assert run['targets'][0]['status'] == 'SUCCEEDED'
        assert store.get_plan(plan['plan_id'])['status'] == PLAN_PENDING_APPROVAL
        with store._connect_readonly() as db:
            db.execute('BEGIN')
            assert db.execute('SELECT COUNT(*) FROM release_approvals').fetchone()[0] == 0
            baseline = NativeCommonBaselineReader(config, store).read(db, plan['plan_id'], run['run_id'])
        assert baseline.status == 'RETAINED_NATIVE_COMMON_BASELINE'
        assert baseline.local_confirmed_writes == 1 and baseline.local_readonly_reuses == 0
        documents = bridge.load_r2_documents(payload['product_id'])
        dashboard = release_control.build_release_dashboard(offer_id=payload['product_id'])
        assert io.mutations == 1
    return documents, dashboard, store, request, io


def historical_completed_context(tmp_path, monkeypatch):
    from test_b4b_publication_preview import multivariant_context
    from test_b4b_common_stage import approve
    from test_service_common_observation import ClosedCommonTransport
    documents, dashboard, store, request, io = multivariant_context(
        tmp_path, monkeypatch, synthetic_technical_authority=False)
    # This is an owned historical APPROVED/run seed, not a current write path.
    approved = approve(request)
    plan = store.get_plan(approved['plan_id'])
    run = store.start_run(plan['plan_id'])
    store.begin_target(run['run_id'], 'miaoshou:COMMON')
    io.detail = deepcopy(ClosedCommonTransport.detail_for(plan['payload']))
    source = release_adapters.readback_miaoshou_common(plan['payload'], post=io.post)
    assert source['verified'] and source['external_writes_performed'] == []
    assert 'native_common_observation' not in source
    evidence = {**source, 'mode': 'owned-historical-normalized-fixture',
                'external_writes_performed': ['miaoshou:COMMON:immutable_plan_write']}
    store.record_target_success(run['run_id'], 'miaoshou:COMMON',
                                external_id=plan['product_id'], readback_evidence=evidence)
    assert io.mutations == 0, 'Historical seeding must never dispatch EDIT'
    assert not plan['payload']['r3_stage_binding'].get('native_preparation_source')
    from shared_platform.publication_common_write_admission import inspect_common_write_admission
    held = inspect_common_write_admission(store.get_plan(plan['plan_id']), store=store)
    assert held['status'] == 'BLOCKED'
    assert held['execution_authority'] is held['final_review_available'] is False
    assert held['external_writes_performed'] == []
    assert set(held['authority_facts'].values()) == {'UNKNOWN'}
    assert held['source_facts']['official_provenance'] == held['source_facts']['budget_status'] == 'UNKNOWN'
    assert held['source_facts']['execution_authority'] is False
    return documents, dashboard, store, {**request, **approved}, io


def historical_reviewed_marketplace(tmp_path, monkeypatch):
    """Original normalized private history; no current EDIT or native grant."""
    documents, dashboard, store, request, io = historical_completed_context(tmp_path, monkeypatch)
    code, result = server._preview_r3_marketplace_stage({'offer_id': request['offer_id']})
    assert code == 200, result
    market = result['marketplace']
    assert market['preview']['status'] == 'READY_FOR_FINAL_REVIEW', market
    assert market['final_review_available'] is False
    assert market['final_review_admission']['execution_authority'] is False
    assert io.mutations == 0
    data = {'offer_id': request['offer_id'], 'plan_id': market['plan']['plan_id'],
            'confirmation_token': market['plan']['confirmation_token'],
            'preview_digest': market['preview']['candidate_digest'],
            'user_approved': True, 'approved_by': 'Kyle'}
    return documents, dashboard, store, data, io, market


@contextmanager
def native_completed_marketplace(tmp_path, monkeypatch, *, pretty_wire=False, task_runtime=False):
    """Real native completed source with a full inert matrix, no final decision.

    Requires the separately reviewed service source in the final composition.
    The owned helper authenticates the actual Windows user; no caller actor,
    fake admission result, prepared nonce or final approval is substituted.
    """
    from test_b4b_release_compiler import policy, INCIDENTS
    from shared_platform import native_common_technical_execution as technical
    from shared_platform import native_sole_final_decision as final
    from shared_platform import publication_r3_image_bridge as bridge
    from shared_platform.publication_runtime_config import capture_startup_config
    from shared_platform.native_sole_final_service import NativeSoleFinalService
    from shared_platform.native_windows_actor import NativeActorServiceConfig
    from shared_platform.ozon_runtime_credentials import PinnedOzonCredentials
    from shared_platform import ozon_runtime_credentials as credentials
    from shared_platform.ozon_stock_warehouse_decision import resolve_warehouse_decision
    documents, dashboard, store, request, io = native_completed_context(tmp_path, monkeypatch, pretty_wire=pretty_wire)
    # Explicit installation is confined to this owned existing database. These
    # original tables/triggers do not make an approval or a nonce.
    with technical._existing_transaction(store) as db:
        for statement in final.TABLES + final.TRIGGERS:
            db.execute(statement)
    market_config = tmp_path/'market-config'
    market_config.mkdir()
    (market_config/'policy.json').write_text(json.dumps(policy(40)), encoding='utf-8')
    (market_config/'incidents.json').write_text(json.dumps(INCIDENTS), encoding='utf-8')
    monkeypatch.setattr(server, 'R3_STARTUP_CONFIG', capture_startup_config(root=market_config, environ={
        'ORBIT_R3_POLICY_PATH':'policy.json', 'ORBIT_R3_INCIDENT_REGISTRY_PATH':'incidents.json'}))
    if 'ozon:RU' in documents['round1_snapshot']['canonical_targets']:
        pinned = PinnedOzonCredentials('12345', 'owned-secret', 'a'*64)
        monkeypatch.setattr(credentials, 'required_pinned_ozon_credentials', lambda:pinned)
        receipt = resolve_warehouse_decision(offer_id=request['offer_id'],
            round1=documents['round1_snapshot'], pinned=pinned,
            post_bound=lambda path, body, **kw:{'warehouses':[
                {'warehouse_id':71, 'status':'active', 'is_kgt':False}]})
        (bridge.REPORTS_ROOT/request['offer_id']/'ozon-warehouse-readback.json').write_text(
            json.dumps(receipt), encoding='utf-8')
    # The original native preparation created this exact source owner. Readers
    # retain it even when this fixture does not need its own worker.
    original_service = server._NATIVE_FINAL_SERVICE
    original_operations = original_service._operations
    engine, profile = original_operations.engine, original_operations.profile
    assert original_service.store is store
    assert profile.root.resolve() == server.ROOT.resolve()
    assert engine.release == {'code_version': profile.version, 'environment': profile.environment,
                              'manifest_digest': profile.manifest_digest}
    operations = original_operations
    owned_worker = None
    if task_runtime:
        from types import SimpleNamespace
        from shared_platform.operations_runtime import OperationsWorker
        owned_worker = OperationsWorker(engine, profile, {})
        operations = SimpleNamespace(profile=profile, engine=engine,
            worker=owned_worker, worker_enabled=False)
    assert operations.engine is original_operations.engine
    assert operations.profile is original_operations.profile
    installed = NativeSoleFinalService(store,
        NativeActorServiceConfig(tmp_path/'owned-native-final', 'Kyle'),
        new_decision_execution_enabled=True, operations=operations)
    try:
        installed.start()
        monkeypatch.setattr(server, '_NATIVE_FINAL_SERVICE', installed)
        code, view = server._preview_r3_marketplace_stage({'offer_id':request['offer_id']})
        assert code == 200, view
        assert view['common']['plan']['status'] == 'PENDING_APPROVAL'
        assert view['common']['retained_baseline_facts']['status'] == 'RETAINED_NATIVE_COMMON_BASELINE'
        with store._connect_readonly() as db:
            db.execute('BEGIN')
            assert db.execute('SELECT COUNT(*) FROM release_approvals').fetchone()[0] == 0
            assert db.execute('SELECT COUNT(*) FROM native_sole_final_nonces').fetchone()[0] == 0
            assert db.execute('SELECT COUNT(*) FROM native_sole_final_decisions').fetchone()[0] == 0
        assert io.mutations == 1
        yield documents, dashboard, store, view, io
    finally:
        installed.close()
        if owned_worker is not None:
            owned_worker.close()
