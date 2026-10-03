"""Formal explicit POST through the real background worker; CLOSED I/O only."""
import hashlib
import json
from pathlib import Path
import sys
import threading
import time

import pytest

from modules.products import server
from shared_platform import native_sole_final_service as channel, native_common_edit_boundary as boundary
from shared_platform import native_sole_final_decision as final, native_common_technical_execution as technical
from shared_platform import operations_launch as launch, publication_rounds, release_control
from shared_platform.bounded_web_server import BoundedThreadingHTTPServer
from shared_platform.native_task_preparation import ExplicitNewTaskWorker
from shared_platform.operations_runtime import OperationsWorker
from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform.worker_category_readonly_cli import ReadonlyCodexResult
from owned_native_parent_profile import owned_profile
from test_round1_auto_freeze import public_settings, complete_source
from test_native_r2_service_consumers import live
from test_round1_workspace_freeze import live as original_live
from test_native_parent_images import R2_SOURCE_PATHS, SOURCE, _write, _image_plan, _bind
from test_native_parent_facts import _events
from test_worker_category_parent_flow import _selection
from test_native_scoped_service_launcher import _serve_seams, _post, _configured_market
from test_native_common_technical_execution import _install_owned
from test_native_common_service_facts import _installed, _closed_signed_transport
from test_publication_paid_entry import FixtureClient, result_bytes


def _fresh_runtime(live):
    """Actual current source/Git identity, with no pre-created publication task."""
    complete_source(live)
    directory = publication_rounds.report_dir(live['offer'])
    path = directory / 'first-review.json'
    packet = json.loads(path.read_bytes())
    packet.pop('image_execution_plan')
    path.write_text(json.dumps(packet), encoding='utf-8')
    for name in R2_SOURCE_PATHS:
        destination = live['root'] / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((SOURCE / name).read_bytes())
        assert hashlib.sha256(destination.read_bytes()).digest() == hashlib.sha256((SOURCE / name).read_bytes()).digest()
    _write(live['root'] / 'config/product_publication_autopilot_policy.json',
           json.loads((SOURCE / 'config/product_publication_autopilot_policy.json').read_bytes()))
    _write(live['root'] / 'config/lingshi.local.json', {'image_qa_model': 'fixture-qa'})
    profile = owned_profile(live['root'])
    engine = WorkbenchEngine(profile.data_root / 'tasks.db', {
        'code_version': profile.version, 'environment': profile.environment,
        'manifest_digest': profile.manifest_digest})
    legacy = OperationsWorker(engine, profile, {})
    assert engine.explicit_new_post_task_ids() == ()
    return {'live': live, 'profile': profile, 'engine': engine, 'worker': legacy}


def _closed_children(monkeypatch):
    # The consumer fixture supplies all seven candidate copies in this actual
    # facts JSONL answer, before the original validator/sidecars/freeze.
    import test_native_parent_images as images
    from shared_platform import worker_category_readonly_cli as cli
    facts = images._child(monkeypatch, _image_plan())
    facts_run = cli.run_readonly_jsonl
    choices = []
    def run(argv, prompt, **kwargs):
        schema = Path(argv[argv.index('--output-schema') + 1])
        if schema.name != 'choice.schema.json':
            return facts_run(argv, prompt, **kwargs)
        data = json.loads((schema.parent / 'category-input.json').read_bytes())
        choices.append((argv, prompt, kwargs, data))
        return ReadonlyCodexResult(0, _events({
            'selection': json.dumps(_selection(data['projection'])), 'missing_inputs': []}))
    monkeypatch.setattr(cli, 'run_readonly_jsonl', run)
    return choices, facts


def _closed_images(monkeypatch, worker):
    from modules.sourcing import brand_image_lingshi_generation as brand, lingshi_client, localized_image_ocr
    from modules.sourcing import localized_image_lingshi_generation as localized
    FixtureClient.calls = []
    FixtureClient.fail_chat = FixtureClient.bad_json = False
    monkeypatch.setattr(lingshi_client, 'LingshiClient', FixtureClient)
    monkeypatch.setattr(brand, '_download_result', result_bytes)
    monkeypatch.setattr(localized_image_ocr, 'detect_english_text_regions', lambda _: [])
    monkeypatch.setitem(localized.generate_localized_reference_image.__kwdefaults__, 'result_loader', result_bytes)
    original = worker.images_adapter._script
    def script(name):
        module = original(name)
        if name == 'prepare_product_images.py':
            monkeypatch.setattr(module, '_download_source', result_bytes)
        return module
    monkeypatch.setattr(worker.images_adapter, '_script', script)


def _wait(engine, task_id, wanted, *, seconds=110):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        current = engine.get(task_id)
        if current['execution_state'] in {wanted, 'failed', 'cancelled'}:
            return current
        time.sleep(.05)
    pytest.fail('Owned worker did not reach exact state: ' + repr(engine.get(task_id)))


@pytest.mark.parametrize('outcome', ['complete', 'common-unknown'])
def test_formal_new_post_actual_worker_reaches_one_final_review_or_retains_original_common_unknown(live, monkeypatch, tmp_path, outcome):
    from shared_platform import publication_r3_image_bridge as bridge, workbench_publication_native as native
    from shared_platform.ozon_runtime_credentials import PinnedOzonCredentials
    from shared_platform.ozon_stock_warehouse_decision import resolve_warehouse_decision
    from test_b4b_release_compiler import INCIDENTS
    v = _fresh_runtime(live)
    choices, facts = _closed_children(monkeypatch)
    actual_start = ExplicitNewTaskWorker.start
    config, operations = _serve_seams(v, monkeypatch, tmp_path)
    # This is the substantive difference from the registration-only test.
    monkeypatch.setattr(ExplicitNewTaskWorker, 'start', actual_start)
    config['agent_executable'] = sys.executable
    _bind(v, tmp_path / 'exact-original-paid-history')
    _install_owned(live['store'], tmp_path)
    signed, _ = _installed(monkeypatch, tmp_path, live['store'])
    _configured_market(signed)
    monkeypatch.setattr(server, 'R3_STARTUP_CONFIG', signed)
    with technical._existing_transaction(live['store']) as db:
        for statement in final.TABLES + final.TRIGGERS:
            db.execute(statement)
    pinned = PinnedOzonCredentials('12345', 'owned-secret', 'a' * 64)
    monkeypatch.setattr('shared_platform.ozon_runtime_credentials.required_pinned_ozon_credentials', lambda: pinned)
    labels = release_control.build_release_dashboard(offer_id=live['offer'])['publication_scope']['selected_labels']
    old = operations.engine.create({'template': 'publication', 'source_key': 'old-not-admitted',
                                    'scope': {'offer_id': '900009999', 'shops': labels}})
    old_events = operations.engine.store.events(old['task_id'])
    payload = {'template': 'publication', 'source_key': 'actual-formal-worker-' + outcome,
               'scope': {'offer_id': live['offer'], 'shops': labels}}
    original_prepare = channel.NativeSoleFinalService.prepare_common_for_task
    common_calls, common_requests = [], []
    def closed_common(service, engine, task, token, profile, binding, plan):
        # Only close the original signed transport and official warehouse READ.
        # Persisting/authority/lease/binding/UNKNOWN stay in the real method.
        documents = server._load_r2_documents_for_service(live['offer'])
        warehouse = resolve_warehouse_decision(offer_id=live['offer'], round1=documents['round1_snapshot'],
            pinned=pinned, post_bound=lambda *a, **k: {'warehouses': [
                {'warehouse_id': 71, 'status': 'active', 'is_kgt': False}]})
        _write(profile.root / 'reports/product-preparation' / live['offer'] / 'ozon-warehouse-readback.json', warehouse)
        common_calls.append(_closed_signed_transport(monkeypatch, service.store, plan['payload'],
                                                     fail_edit=outcome == 'common-unknown'))
        common_requests.append(plan['plan_id'])
        return original_prepare(service, engine, task, token, profile, binding, plan)
    monkeypatch.setattr(channel.NativeSoleFinalService, 'prepare_common_for_task', closed_common)
    original_serve = BoundedThreadingHTTPServer.serve_forever
    finished = []
    def drive(http):
        worker = operations.new_task_worker
        assert worker.thread.is_alive() and not operations.worker_enabled
        assert not worker.owns(old['task_id']) and worker.status()['historical_task_scan'] is False
        worker.interval = .1
        _closed_images(monkeypatch, worker)
        listener = threading.Thread(target=lambda: original_serve(http), daemon=True)
        listener.start()
        try:
            code, value = _post(http, '/api/orbit/tasks', payload)
            assert code == 201, value
            task_id = value['task']['task_id']
            assert worker.owns(task_id)
            code, repeated = _post(http, '/api/orbit/tasks', payload)
            assert repeated['task']['task_id'] == task_id
            current = _wait(operations.engine, task_id, 'waiting_user' if outcome == 'complete' else 'reconciliation_required')
            assert current['steps'][0]['state'] == current['steps'][1]['state'] == 'completed', current
            assert current['current_step'] == 'release' and native.read_frozen(current, v['profile'])
            assert native.read_images(current, v['profile'])['native_r2'] == current['steps'][1]['checkpoint']['native_r2']
            assert len(choices) == len(facts) == 1 and len(live['fake'].calls) == 7
            assert len(common_requests) == 1 and sum(c.count(boundary.EDIT_PATH) for c in common_calls) == 1
            assert sum(row['kind'] == 'brand' for row in FixtureClient.calls) == 14
            events = operations.engine.store.events(task_id)
            assert sum(e['event_type'] == 'explicit_new_post_preparation' for e in events) == 1
            assert operations.engine.store.events(old['task_id']) == old_events
            assert operations.engine.get(old['task_id'])['execution_state'] == 'queued'
            stored = http.native_final_review.store.get_plan(common_requests[0])
            assert stored['status'] == 'PENDING_APPROVAL' and stored['approval'] is None
            with http.native_final_review.store._connect_readonly() as db:
                rows = db.execute('SELECT * FROM release_runs WHERE plan_id=?', (stored['plan_id'],)).fetchall()
                assert len(rows) == 1 and rows[0]['approval_id'] is None
                original_run = dict(rows[0])
                source = json.loads(original_run['technical_admission_json'])
                assert source['offer_id'] == live['offer'] and source['payload_digest'] == stored['payload_digest']
                assert source['plan_id'] == stored['plan_id'] and source['maximum_confirmed_writes'] == 1
                assert original_run['technical_execution_state'] == ('CONFIRMED_WRITE' if outcome == 'complete' else 'UNKNOWN')
                target = db.execute('SELECT target_label,attempts FROM release_target_runs WHERE run_id=?',
                                    (original_run['run_id'],)).fetchall()
                assert [tuple(row) for row in target] == [('miaoshou:COMMON', 1)]
                assert db.execute('SELECT COUNT(*) FROM release_approvals').fetchone()[0] == 0
            if outcome == 'complete':
                assert current['execution_state'] == 'waiting_user' and current['required_action']['kind'] == 'review', current
                code, view = server._publication_stages_for_request({'offer_id': live['offer']})
                assert code == 200 and view['marketplace']['final_review_available'] is True, view
                review = view['marketplace']['native_final_review']
                assert set(review['targets']) == set(labels) - {'miaoshou:COMMON'}
                assert review['approval_saved'] is review['execution_authority'] is False
            else:
                assert current['execution_state'] == 'reconciliation_required' and not current['required_action'], current
                attempts = [row for row in operations.engine.store.events(task_id) if row['event_type'] == 'execution_failed']
                assert not attempts
                observations = [row for row in events if row['event_type'] == 'domain_unknown_observed']
                assert observations and observations[0]['detail']['request_sent_by_this_observer'] is False
            # Duplicate wake/polls may observe the original ledger; never redispatch.
            posts = list(FixtureClient.calls)
            writes = sum(c.count(boundary.EDIT_PATH) for c in common_calls)
            worker.wake()
            time.sleep(.35)
            assert _post(http, '/api/orbit/tasks', payload)[1]['task']['task_id'] == task_id
            time.sleep(.35)
            assert FixtureClient.calls == posts and sum(c.count(boundary.EDIT_PATH) for c in common_calls) == writes
            reread = http.native_final_review.store.get_run(original_run['run_id'])
            assert reread['run_id'] == original_run['run_id'] and reread['approval_id'] is None
            assert reread['targets'][0]['attempts'] == 1
            deadline = time.monotonic() + 5
            while any(not f.done() for f in worker.inflight.values()) and time.monotonic() < deadline:
                time.sleep(.02)
            assert all(f.done() for f in worker.inflight.values())
            finished.append(worker)
        finally:
            http.shutdown()
            listener.join(timeout=5)
            assert not listener.is_alive()
    monkeypatch.setattr(BoundedThreadingHTTPServer, 'serve_forever', drive)
    launch.serve(config, v['profile'].root)
    assert finished and not finished[0].thread.is_alive()
    assert all(f.done() for f in finished[0].inflight.values())
    assert not hasattr(operations, 'new_task_worker')
