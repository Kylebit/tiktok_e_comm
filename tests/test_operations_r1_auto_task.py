"""Synthetic task-to-domain R1 adoption; no worker, service, or marketplace write."""
from types import SimpleNamespace
from copy import deepcopy
import http.client
import json
import time

import pytest

from modules.sourcing import new_product_workbench as workbench
from shared_platform import operations_publication
from shared_platform import operations_publication_prepare as adapter
from shared_platform import operations_runtime, publication_rounds, publication_autopilot
from shared_platform.workbench_engine import WorkbenchEngine
from test_round1_auto_freeze import complete_source, public_settings
from test_round1_workspace_freeze import live, prepared


def _task(live, packet):
    profile = SimpleNamespace(root=live['root'], data_root=live['root'] / 'data/operations/stable',
                              version='synthetic-r1-task', environment='stable',
                              manifest_digest='synthetic-manifest')
    release = {'code_version': profile.version, 'environment': profile.environment,
               'manifest_digest': profile.manifest_digest}
    engine = WorkbenchEngine(profile.data_root / 'tasks.db', release)
    engine.register_executor('synthetic-worker', ['publication'], engine.release)
    task = engine.create({'template': 'publication', 'source_key': 'synthetic-' + live['offer'],
                          'scope': {'offer_id': live['offer'],
                                    'shops': packet['packet']['target_selection']['requested']}})
    token = engine.claim(task['task_id'], 'synthetic-worker')['lease_token']
    return engine, engine.get(task['task_id']), token, profile


def _prepared_review(monkeypatch, packet):
    monkeypatch.setattr(adapter, '_current', lambda *_: ({'review_path': 'synthetic'}, packet['packet']))
    monkeypatch.setattr(adapter, '_review', lambda *_: {
        'prepared_reference': packet['prepared_reference'],
        'round1_prepared_review': packet})


def test_complete_r1_task_freezes_technically_without_intermediate_review(live, monkeypatch):
    complete_source(live)
    packet, _ = prepared(live)
    engine, task, token, profile = _task(live, packet)
    _prepared_review(monkeypatch, packet)
    monkeypatch.setattr(operations_runtime, '_R1_FILESYSTEM_BOUNDARY_VERIFIED', True)

    run, _ = operations_publication.bindings()
    run(engine, task, token, profile)
    observed = engine.get(task['task_id'])
    assert observed['current_step'] == 'images' and observed['execution_state'] == 'running'
    assert not observed.get('required_action') and not observed.get('pending_observation')
    assert workbench.load_state(live['offer'])['product_approval']['approved_by'] == publication_rounds.AUTOPILOT_ACTOR
    assert (observed['steps'][0]['checkpoint']['native_r1']['prepared_reference']
            == packet['prepared_reference'])
    assert not [event for event in engine.store.events(task['task_id'])
                if event['event_type'] == 'user_action_required']
    live['operations_runtime'].engine = engine
    live['operations_runtime'].profile = profile
    live['restart']()
    connection = http.client.HTTPConnection('127.0.0.1', live['port'], timeout=15)
    try:
        connection.request('GET', '/api/product-workspace/dashboard?offer_id=' + live['offer'])
        response = connection.getresponse()
        dashboard = json.loads(response.read())
    finally:
        connection.close()
    assert response.status == 200
    assert dashboard['round1_prepared_review']['status'] == 'FROZEN'
    assert dashboard['round1_prepared_review']['prepared_reference'] == packet['prepared_reference']


def test_default_unverified_filesystem_stops_before_technical_decision(live, monkeypatch):
    complete_source(live)
    packet, _ = prepared(live)
    engine, task, token, profile = _task(live, packet)
    _prepared_review(monkeypatch, packet)
    assert operations_runtime._R1_FILESYSTEM_BOUNDARY_VERIFIED is False

    run, observe = operations_publication.bindings()
    run(engine, task, token, profile)
    observed = engine.get(task['task_id'])
    assert observed['execution_state'] == 'reconciliation_required'
    assert not observed.get('required_action') and not observed.get('pending_observation')
    assert not workbench.load_state(live['offer']).get('product_approval')
    assert not (live['root'] / 'reports/product-preparation' / live['offer'] /
                'round1-auto-decision.json').exists()
    assert observe(engine, engine.get(task['task_id']), profile) is False
    assert engine.get(task['task_id'])['execution_state'] == 'reconciliation_required'


def test_partial_technical_cas_reconciles_without_reprompt_or_second_write(live, monkeypatch):
    complete_source(live)
    packet, _ = prepared(live)
    engine, task, token, profile = _task(live, packet)
    _prepared_review(monkeypatch, packet)
    monkeypatch.setattr(operations_runtime, '_R1_FILESYSTEM_BOUNDARY_VERIFIED', True)
    from shared_platform import immutable_approval_files
    original = immutable_approval_files.persist_immutable_bytes

    def fail_snapshot(path, content, *, root):
        if path.name == 'round1-approved-snapshot.json':
            raise OSError('synthetic interrupted snapshot persistence')
        return original(path, content, root=root)

    monkeypatch.setattr(immutable_approval_files, 'persist_immutable_bytes', fail_snapshot)
    run, _ = operations_publication.bindings()
    run(engine, task, token, profile)
    observed = engine.get(task['task_id'])
    assert observed['execution_state'] == 'reconciliation_required'
    assert observed['current_step'] == 'facts' and not observed.get('required_action')
    assert workbench.load_state(live['offer'])['product_approval']['approved_by'] == publication_rounds.AUTOPILOT_ACTOR
    assert not (live['root'] / 'reports/product-preparation' / live['offer'] /
                'round1-approved-snapshot.json').exists()
    assert not [event for event in engine.store.events(task['task_id'])
                if event['event_type'] == 'user_action_required']

    # A new worker process loads the same task DB after the local disk fault
    # is repaired. The active policy may have rotated since the old CAS.
    monkeypatch.setattr(immutable_approval_files, 'persist_immutable_bytes', original)
    rotated = dict(publication_autopilot.load_autopilot_policy())
    rotated['policy_id'] += '-rotated'
    monkeypatch.setattr(publication_autopilot, 'load_autopilot_policy', lambda: rotated)
    monkeypatch.setattr(workbench, 'save_state', lambda *_args, **_kwargs:
                        pytest.fail('technical Product Center CAS must not run twice'))
    restarted = WorkbenchEngine(profile.data_root / 'tasks.db', task['version'])
    run, observe = operations_publication.bindings()
    monkeypatch.setattr(operations_runtime, 'runtime_matches', lambda *_: True)
    original_claim = restarted.claim
    monkeypatch.setattr(restarted, 'claim', lambda *_args, **_kwargs: None)
    worker = operations_runtime.OperationsWorker(restarted, profile,
        {'publication': run}, observe=observe, interval=0.05)
    worker.start()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and restarted.get(task['task_id'])['execution_state'] != 'queued':
            time.sleep(0.02)
        assert restarted.get(task['task_id'])['execution_state'] == 'queued'
        assert task['task_id'] in worker.r1_recovery_seen
    finally:
        worker.close()
    assert len([event for event in restarted.store.events(task['task_id'])
                if event['event_type'] == 'r1_technical_recovered']) == 1
    monkeypatch.setattr(restarted, 'claim', original_claim)
    # The real adapter consumes only the recovered domain snapshot; facts
    # agent work and a second human review are not invoked.
    monkeypatch.setattr(adapter, '_current', lambda *_:
                        pytest.fail('recovery must not rerun the facts agent or preparation'))
    restarted.register_executor('resume-worker', ['publication'], restarted.release)
    token = restarted.claim(task['task_id'], 'resume-worker')['lease_token']
    run(restarted, restarted.get(task['task_id']), token, profile)
    resumed = restarted.get(task['task_id'])
    assert resumed['current_step'] == 'images'
    assert not [event for event in restarted.store.events(task['task_id'])
                if event['event_type'] == 'user_action_required']


def test_copied_technical_intent_cannot_resume_another_task(live, monkeypatch):
    complete_source(live)
    packet, _ = prepared(live)
    engine, task, token, profile = _task(live, packet)
    _prepared_review(monkeypatch, packet)
    monkeypatch.setattr(operations_runtime, '_R1_FILESYSTEM_BOUNDARY_VERIFIED', False)
    run, _ = operations_publication.bindings()
    run(engine, task, token, profile)
    assert engine.get(task['task_id'])['execution_state'] == 'reconciliation_required'
    second = engine.create({'template': 'publication', 'source_key': 'copied-' + live['offer'],
                            'scope': task['scope']})
    original_checkpoint = engine.get(task['task_id'])['checkpoint']
    forged_checkpoint = deepcopy(original_checkpoint)
    forged_intent = forged_checkpoint['r1_auto_intent']
    forged_intent['task_id'] = second['task_id']
    forged_intent.pop('intent_digest')
    forged_intent['intent_digest'] = publication_rounds.canonical_digest(forged_intent)
    with engine.transaction() as conn:
        conn.execute('UPDATE workbench_execution SET state=?,checkpoint_json=? WHERE task_id=?',
                     ('reconciliation_required', json.dumps(forged_checkpoint), second['task_id']))
        engine._event(conn, second['task_id'], 'checkpoint_saved',
                      {'checkpoint': forged_checkpoint})
    with pytest.raises(ValueError, match='another task'):
        engine.read_r1_technical_recovery(second['task_id'])


def test_uncommitted_decision_does_not_gain_new_policy_authority(live, monkeypatch):
    complete_source(live)
    packet, _ = prepared(live)
    engine, task, token, profile = _task(live, packet)
    _prepared_review(monkeypatch, packet)
    monkeypatch.setattr(operations_runtime, '_R1_FILESYSTEM_BOUNDARY_VERIFIED', True)
    original_save = workbench.save_state
    monkeypatch.setattr(workbench, 'save_state', lambda *_args, **_kwargs:
                        (_ for _ in ()).throw(OSError('synthetic pre-CAS failure')))
    run, observe = operations_publication.bindings()
    run(engine, task, token, profile)
    assert engine.get(task['task_id'])['execution_state'] == 'reconciliation_required'
    assert not workbench.load_state(live['offer']).get('product_approval')
    monkeypatch.setattr(workbench, 'save_state', original_save)
    rotated = dict(publication_autopilot.load_autopilot_policy())
    rotated['policy_id'] += '-rotated-before-CAS'
    monkeypatch.setattr(publication_autopilot, 'load_autopilot_policy', lambda: rotated)
    assert observe(engine, engine.get(task['task_id']), profile) is False
    assert engine.get(task['task_id'])['execution_state'] == 'reconciliation_required'
    assert not workbench.load_state(live['offer']).get('product_approval')
    assert not (live['root'] / 'reports/product-preparation' / live['offer'] /
                'round1-approved-snapshot.json').exists()
