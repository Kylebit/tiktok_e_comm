"""Original images, COMMON and sole-final readers with CLOSED transports."""
from types import SimpleNamespace
import json

import pytest

from modules.products import server
from shared_platform import workbench_publication_native as native
from shared_platform import native_sole_final_service as channel
from shared_platform import native_common_technical_execution as technical
from shared_platform import native_sole_final_decision as final
from shared_platform import publication_r3_image_bridge as bridge
from shared_platform import release_store
from shared_platform.native_windows_actor import NativeActorServiceConfig
from test_round1_workspace_freeze import live as original_live
from test_round1_auto_freeze import public_settings
from test_native_parent_images import images_task, FixtureClient
from test_native_parent_images_phase_continuation import _closed
from test_native_common_retained_review_graph import _propose_market_inputs
from test_native_common_technical_execution import _install_owned
from test_native_common_service_facts import _installed, _closed_signed_transport


@pytest.fixture
def live(original_live, monkeypatch):
    # All category/copy proposals precede the original capture and freeze.
    _propose_market_inputs(original_live, monkeypatch)
    import test_native_parent_images as images
    import test_round1_sidecar_convergence as sidecar
    original_child = images._child
    def child_with_market_candidates(patch, image_plan, mutate=None):
        def propose(body, schema_path, data):
            targets = [label for label in data['binding']['targets'] if label != 'miaoshou:COMMON']
            candidate = sidecar.candidate(targets[0])
            assert {row['target'] for row in candidate['target_candidates']} == set(targets)
            body['candidate_plan'] = json.dumps(candidate)
            if mutate is not None:
                return mutate(body, schema_path, data)
        return original_child(patch, image_plan, propose)
    # The actual typed child, rather than the unused legacy candidate factory,
    # returns the proposed plan to the original validator and fixed sidecar.
    monkeypatch.setattr(images, '_child', child_with_market_candidates)
    return original_live


def _runtime(v, monkeypatch, tmp_path, *, enabled=False):
    operations = SimpleNamespace(engine=v['engine'], profile=v['profile'], worker=v['worker'], worker_enabled=False)
    runtime = channel.NativeSoleFinalService(v['live']['store'],
        NativeActorServiceConfig(tmp_path / 'native-final-owner', 'Kyle'), operations=operations,
        new_decision_execution_enabled=enabled)
    monkeypatch.setattr(server, '_NATIVE_FINAL_SERVICE', runtime)
    return runtime


def _complete_images(v, monkeypatch):
    client, _, _, _ = _closed(v, monkeypatch)
    client.ready = True
    result = v['worker'].images_adapter.recover_images(v['task'], token=v['token'])
    assert result['status'] == 'prepared', result
    v['worker'].adapters['publication'](v['engine'], v['engine'].get(v['task']['task_id']), v['token'], v['profile'])
    task = v['engine'].get(v['task']['task_id'])
    assert task['steps'][1]['state'] == 'completed' and task['current_step'] == 'release'
    return task


def test_original_images_common_and_native_sole_final_read_the_same_prepared_owner(images_task, monkeypatch, tmp_path, request):
    from test_b4b_release_compiler import policy, INCIDENTS
    from shared_platform.ozon_runtime_credentials import PinnedOzonCredentials
    from shared_platform.ozon_stock_warehouse_decision import resolve_warehouse_decision
    v = images_task
    task = _complete_images(v, monkeypatch)
    runtime = _runtime(v, monkeypatch, tmp_path, enabled=True)
    request.addfinalizer(runtime.close)
    offer = task['scope']['offer_id']
    directory = v['profile'].root / 'reports/product-preparation' / offer
    originals = {path: path.read_bytes() for path in directory.glob('*.json')}
    task_database = v['profile'].data_root / 'tasks.db'
    task_bytes = task_database.read_bytes()
    with monkeypatch.context() as readonly:
        readonly.setattr(v['engine'], 'transaction', lambda: pytest.fail('R2 GET must not open a schema/write transaction'))
        documents = server._load_r2_documents_for_service(offer)
    assert task_database.read_bytes() == task_bytes
    identity = bridge.validate_r2_identity(documents)
    assert documents['first_review']['status'] == 'FIRST_REVIEW_READY'
    prepared_copies = {row['target']: row['copy'] for row in documents['first_review']['targets']
                       if row['target'] != 'miaoshou:COMMON'}
    assert set(prepared_copies) == {label for label in task['scope']['shops'] if label != 'miaoshou:COMMON'}
    assert prepared_copies['tiktok:LH_PH']['language'] == 'en'
    assert all(prepared_copies['tiktok:LH_PH'][key] for key in ('title', 'description'))
    candidate_sidecar = json.loads((directory / 'first-review-candidate-plan.json').read_bytes())
    assert {row['target']: row['copy'] for row in candidate_sidecar['target_candidates']} == prepared_copies
    assert json.loads((directory / 'first-review.json').read_bytes())['status'] == 'DECISION_REQUIRED'
    assert identity == task['steps'][1]['checkpoint']['native_r2']
    code, preview = server._preview_r3_common_stage({'offer_id': offer,
        'release_stage': 'R3_COMMON', 'publication_targets': ['miaoshou:COMMON']})
    assert code == 200 and preview['ok'] is True, preview
    common = preview['common']['plan']
    assert common['payload']['r3_stage_binding']['r2_identity'] == identity
    assert runtime.store.get_plan(common['plan_id']) is None
    _install_owned(runtime.store, tmp_path)
    _installed(monkeypatch, tmp_path, runtime.store)
    # Both original stages are installed before the genuine automatic handoff.
    # The COMMON-only fixture intentionally has no marketplace incident file.
    from shared_platform.publication_runtime_config import capture_startup_config, diagnose
    from shared_platform import ozon_runtime_credentials as credentials
    market_config = tmp_path / 'market-config'
    market_config.mkdir()
    (market_config / 'policy.json').write_text(json.dumps(policy(40)), encoding='utf-8')
    (market_config / 'incidents.json').write_text(json.dumps(INCIDENTS), encoding='utf-8')
    startup = capture_startup_config(root=market_config, environ={
        'ORBIT_R3_POLICY_PATH': 'policy.json', 'ORBIT_R3_INCIDENT_REGISTRY_PATH': 'incidents.json'})
    monkeypatch.setattr(server, 'R3_STARTUP_CONFIG', startup)
    configuration, configured = diagnose(startup)
    assert configuration['status'] == 'CONFIGURED' and configured
    pinned = PinnedOzonCredentials('12345', 'owned-secret', 'a' * 64)
    monkeypatch.setattr(credentials, 'required_pinned_ozon_credentials', lambda: pinned)
    warehouse = resolve_warehouse_decision(offer_id=offer, round1=documents['round1_snapshot'],
        pinned=pinned,
        post_bound=lambda *a, **k: {'warehouses': [{'warehouse_id': 71, 'status': 'active', 'is_kgt': False}]})
    (directory / 'ozon-warehouse-readback.json').write_text(json.dumps(warehouse), encoding='utf-8')
    with technical._existing_transaction(runtime.store) as db:
        for statement in final.TABLES + final.TRIGGERS:
            db.execute(statement)
    calls = _closed_signed_transport(monkeypatch, runtime.store, common['payload'])
    code, missing = server._prepare_miaoshou_release({'offer_id': offer,
        'plan_id': common['plan_id'], 'confirm_miaoshou_write': True})
    assert code==409 and missing['error']=='COMMON_TECHNICAL_PLAN_MISSING'
    assert calls==[] and runtime.store.get_plan(common['plan_id']) is None
    runtime.start()
    from shared_platform import operations_publication_common as workflow
    workflow.run(v['engine'],task,v['token'],v['profile'])
    persisted=runtime.store.get_plan(common['plan_id'])
    assert persisted['status']==release_store.PLAN_PENDING_APPROVAL and persisted['approval'] is None
    assert all(persisted[key]==common[key] for key in ('plan_id','payload_digest','payload'))
    code, completed = server._prepare_miaoshou_release({'offer_id': offer,
        'plan_id': common['plan_id'], 'confirm_miaoshou_write': True})
    assert code == 200 and completed['native_run']['state'] == 'CONFIRMED_WRITE', completed
    material = bridge.build_marketplace_review_material(documents, runtime.store.get_plan(common['plan_id']),
        runtime.store.get_run(completed['native_run']['run_id']), policy=policy(40), incidents=INCIDENTS,
        ozon_stock_decision=warehouse['decision'], category_store=runtime.store)
    from shared_platform.release_store import _target_labels
    markets = list(_target_labels([label for label in documents['round1_snapshot']['canonical_targets'] if label != 'miaoshou:COMMON']))
    assert material['payload']['targets'] == markets and len(markets) == 7
    assert material['payload']['r3_marketplace_binding']['documents'] == documents
    assert common['payload']['r3_stage_binding']['marketplace_targets'] == documents['round1_snapshot']['canonical_targets']
    assert 'miaoshou:COMMON' not in material['payload']['product_facts']['content_by_target']
    market = runtime.store.create_plan(material['payload'])
    runtime.start()
    try:
        review = runtime.prepare(market['plan_id'])
        assert review['review']['targets'] == tuple(markets)
        assert review['review']['round2_digest'] == identity['identity_digest']
        assert review['execution_authority'] is False
        # The genuine immutable R1 keeps its original order and full COMMON
        # member; only the derived market display uses the existing store order.
        from copy import deepcopy
        import hashlib
        from shared_platform.r3_common_source_facts import NativeCommonSourceReader
        from shared_platform.native_common_retained_completion import read_retained_completion
        from shared_platform.r3_frozen_review_producer import rebuild_domain_review_graph, DomainReviewBlocked
        assert markets == list(_target_labels([label for label in documents['round1_snapshot']['canonical_targets']
                                              if label != 'miaoshou:COMMON']))
        assert material['candidate']['target_labels'] == markets
        assert [row['target_label'] for row in material['candidate']['review_manifest']['targets']] == markets
        original_market = deepcopy(market)
        original_common = runtime.store.get_plan(common['plan_id'])
        original_run = runtime.store.get_run(completed['native_run']['run_id'])
        before_guards = runtime.store.path.read_bytes()
        with runtime.store._connect_readonly() as db:
            db.execute('BEGIN')
            reader = NativeCommonSourceReader(runtime.store)
            completion = read_retained_completion(reader, db, original_common['plan_id'], original_run['run_id'])
            graph = rebuild_domain_review_graph(original_common, original_run, market,
                native_completion=completion, category_store=runtime.store, category_connection=db)
            assert graph.targets == tuple(markets)
            for changed_targets in (markets[:-1], markets + ['tiktok:HB_PH'], markets + [markets[0]],
                                    markets + ['tiktok:UNKNOWN'], list(reversed(markets))):
                changed = deepcopy(market)
                changed['targets'] = changed['payload']['targets'] = changed_targets
                raw = json.dumps(changed['payload'], ensure_ascii=False, sort_keys=True,
                    separators=(',', ':'), allow_nan=False).encode('utf-8')
                changed['payload_digest'] = hashlib.sha256(raw).hexdigest()
                with pytest.raises(DomainReviewBlocked, match='^DOMAIN_PLAN_SOURCE_SCOPE_CHANGED$'):
                    rebuild_domain_review_graph(original_common, original_run, changed,
                        native_completion=completion, category_store=runtime.store, category_connection=db)
            changed = deepcopy(market)
            changed['payload']['r3_marketplace_binding']['documents']['round1_snapshot']['offer_id'] = '900001234'
            raw = json.dumps(changed['payload'], ensure_ascii=False, sort_keys=True,
                separators=(',', ':'), allow_nan=False).encode('utf-8')
            changed['payload_digest'] = hashlib.sha256(raw).hexdigest()
            with pytest.raises(DomainReviewBlocked):
                rebuild_domain_review_graph(original_common, original_run, changed,
                    native_completion=completion, category_store=runtime.store, category_connection=db)
        assert runtime.store.path.read_bytes() == before_guards and market == original_market
        decision = runtime.decide(nonce=review['nonce'], review_digest=review['review_digest'])
        channel.check_current_rounds(runtime.store, market['plan_id'], operations=runtime._operations)
        with runtime.operation() as service:
            assert service.recheck(decision['decision_id'])['execution_recheck_succeeded'] is True
            from shared_platform import native_sole_final_execution as execution
            active = execution._NativeExecution(service, decision['decision_id'], runtime)
            active.plan_id = market['plan_id']
            token = execution._ACTIVE.set(active)
            try:
                data = {'offer_id': offer, 'plan_id': market['plan_id'],
                        'confirmation_token': market['confirmation_token']}
                gate, failure = active.gate(data, store=runtime.store)
                assert failure is None and gate['payload'] == market['payload']
                with v['engine'].transaction() as db:
                    db.execute("DELETE FROM workbench_events WHERE task_id=? AND event_type='checkpoint_saved'",
                               (task['task_id'],))
                gate, failure = active.gate(data, store=runtime.store)
                assert gate is None and failure[0] == 409
                assert failure[1]['error'] == 'NATIVE_R2_ORIGINAL_OWNER_UNAVAILABLE'
                assert failure[1]['external_writes_performed'] == []
            finally:
                execution._ACTIVE.reset(token)
        assert runtime.store.get_plan(common['plan_id'])['status'] == release_store.PLAN_PENDING_APPROVAL
        assert calls.count('/open/v1/product/common_collect_box/common_collect_box/edit_common_collect_box_detail') == 1
        assert all(path.read_bytes() == raw for path, raw in originals.items())
    finally:
        runtime.close()


@pytest.mark.parametrize('drift', ['missing-owner', 'multiple-owners', 'owner-version', 'owner-offer'])
def test_native_origin_failure_never_falls_back_to_a_raw_or_another_offer_reader(images_task, monkeypatch, tmp_path, drift):
    v = images_task
    runtime = _runtime(v, monkeypatch, tmp_path)
    task = v['engine'].get(v['task']['task_id'])
    reference = native.read_frozen(task, v['profile'])['prepared_reference']
    if drift == 'multiple-owners':
        second = v['engine'].create({'template': 'publication', 'source_key': 'foreign-prepared-owner',
            'scope': task['scope']})
    with v['engine'].transaction() as db:
        if drift == 'missing-owner':
            db.execute("DELETE FROM workbench_events WHERE task_id=? AND event_type='checkpoint_saved'", (task['task_id'],))
            error = 'NATIVE_R2_ORIGINAL_OWNER_UNAVAILABLE'
        elif drift == 'multiple-owners':
            # A copied provenance row is not a second native origin authority.
            v['engine']._event(db, second['task_id'], 'checkpoint_saved',
                {'checkpoint': {'native_preparation': {'prepared_reference': reference}}})
            error = 'NATIVE_R2_ORIGINAL_OWNER_AMBIGUOUS'
        elif drift == 'owner-offer':
            db.execute('UPDATE workbench_execution SET scope_json=? WHERE task_id=?',
                (json.dumps({**task['scope'], 'offer_id': '999999999'}), task['task_id']))
            error = 'NATIVE_R2_ORIGINAL_OWNER_OFFER_CHANGED'
        else:
            changed = {**task['version'], 'code_version': 'foreign-source'}
            db.execute('UPDATE workbench_execution SET version_json=? WHERE task_id=?',
                (json.dumps(changed), task['task_id']))
            error = 'task belongs to another pinned runtime version'
    before = list(FixtureClient.calls)
    monkeypatch.setattr(bridge, 'load_r2_documents', lambda *a, **k: pytest.fail('No raw or foreign fallback'))
    with pytest.raises(ValueError, match=error):
        server._load_r2_documents_for_service(task['scope']['offer_id'])
    assert FixtureClient.calls == before and runtime.store.get_plan('missing-native-plan') is None


def test_request_dictionary_and_closed_service_cannot_select_a_native_r2_owner(images_task, monkeypatch, tmp_path):
    v = images_task
    with pytest.raises(ValueError, match='NATIVE_R2_SERVICE_RUNTIME_REQUIRED'):
        native.load_service_r2_documents(v['task']['scope']['offer_id'], {'engine': v['engine'], 'profile': v['profile']})
    runtime = _runtime(v, monkeypatch, tmp_path)
    runtime._closed = True
    with pytest.raises(ValueError, match='NATIVE_R2_SERVICE_CLOSED'):
        server._load_r2_documents_for_service(v['task']['scope']['offer_id'])


def test_immutable_automatic_source_without_current_reference_cannot_become_legacy(images_task, monkeypatch, tmp_path):
    v = images_task
    _runtime(v, monkeypatch, tmp_path)
    offer = v['task']['scope']['offer_id']
    path = v['profile'].root / 'data/new_product_workbench' / (offer + '.json')
    original = path.read_bytes()
    state = json.loads(original)
    for key in ('round1_prepared_reference', 'approved_by', 'approval_authority', 'decision_digest'):
        state['product_approval'].pop(key, None)
    path.write_text(json.dumps(state), encoding='utf-8')
    monkeypatch.setattr(bridge, 'load_r2_documents', lambda *a, **k: pytest.fail('No legacy downgrade'))
    try:
        with pytest.raises(ValueError, match='NATIVE_R2_ORIGINAL_REFERENCE_UNAVAILABLE'):
            server._load_r2_documents_for_service(offer)
    finally:
        path.write_bytes(original)
