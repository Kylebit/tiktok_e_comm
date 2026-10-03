"""Real leased worker/category/R1 producers with a closed read-only JSONL child."""
import json
import os
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

from modules.products import server
from shared_platform import operations_runtime, publication_rounds, release_control
from shared_platform import workbench_publication_native as native
from shared_platform.native_parent_facts import SCHEMA
from shared_platform.native_task_preparation import ExplicitNewTaskWorker, NativeR1FilesystemBoundary, install_explicit_new_task_preparation
from shared_platform.operations_runtime import RuntimeProfile
from shared_platform.workbench_engine import WorkbenchEngine
from shared_platform.worker_category_admission import request_id
from shared_platform.worker_category_bridge import WorkerCategoryBridge
from shared_platform.worker_category_readonly_cli import ReadonlyCodexResult
from test_round1_workspace_freeze import live
from test_round1_auto_freeze import complete_source, public_settings
from test_round1_initial_options import choice
from owned_native_parent_profile import owned_profile


def _leased(live, monkeypatch):
    complete_source(live)
    directory = publication_rounds.report_dir(live['offer'])
    initial = json.loads((directory / 'first-review.json').read_text(encoding='utf-8'))
    proposed_images = initial.pop('image_execution_plan')
    # An actual incomplete producer input, before any preparation/freeze.
    (directory / 'first-review.json').write_text(json.dumps(initial), encoding='utf-8')
    profile = owned_profile(live['root'])
    engine = WorkbenchEngine(profile.data_root / 'tasks.db', {
        'code_version': profile.version, 'environment': profile.environment,
        'manifest_digest': profile.manifest_digest})
    boundary = NativeR1FilesystemBoundary(profile)
    runtime = SimpleNamespace(engine=engine, profile=profile)
    # Suppress only the background poll loop: actual startup factory, bindings,
    # lease, directory hold, adapter and all domain producers remain real.
    with monkeypatch.context() as startup:
        startup.setattr(ExplicitNewTaskWorker, 'start', lambda self: None)
        worker = install_explicit_new_task_preparation(runtime)
    assert runtime.new_task_worker is worker
    boundary = worker.boundary
    labels = release_control.build_release_dashboard(offer_id=live['offer'])['publication_scope']['selected_labels']
    task = worker.create({'template': 'publication', 'source_key': 'real-typed-parent',
                         'scope': {'offer_id': live['offer'], 'shops': labels}})
    engine.register_executor(worker.worker_id, ['publication'], engine.release)
    token = engine.claim(task['task_id'], worker.worker_id, ttl=300)['lease_token']
    scope = {'offer_id': live['offer'], 'product_center_revision': 7,
             'requested_targets': sorted(labels), 'source_region': 'MY'}
    ctx = server._round1_category_context(scope)
    body = {**scope, 'schema_version': 'round1-category-options-request/v1',
            'context_digest': ctx['context_digest'],
            'account_identity_digest': ctx['source_account']['account_identity_digest']}
    body['request_id'] = request_id(task['task_id'], 'options', body)
    category = WorkerCategoryBridge(engine, live['store'],
        lambda action, request, origin: server._round1_category_initial_request(
            action, request, worker_origin=origin))
    options = category.execute(task_id=task['task_id'], worker_id=worker.worker_id,
        lease_token=token, action='options', body=body)
    assert options['status'] == 'SUCCEEDED', options
    selected = choice(body, options['domain_response'][1])
    selected['request_id'] = request_id(task['task_id'], 'capture', selected)
    captured = category.execute(task_id=task['task_id'], worker_id=worker.worker_id,
        lease_token=token, action='capture', body=selected)
    assert captured['status'] == 'SUCCEEDED', captured
    monkeypatch.setenv('ORBIT_OPERATIONS_AGENT_EXECUTABLE', sys.executable)
    return engine, profile, worker, token, proposed_images


def _events(body):
    text = body if isinstance(body, str) else json.dumps(body)
    return '\n'.join(json.dumps(event) for event in (
        {'type': 'thread.started', 'thread_id': 'owned-typed-child'},
        {'type': 'turn.started'},
        {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': text}},
        {'type': 'turn.completed'})) + '\n'


def _child(monkeypatch, images, mutate=None):
    calls = []
    def run(argv, prompt, **kwargs):
        schema_path = Path(argv[argv.index('--output-schema') + 1])
        data = json.loads((schema_path.parent / 'parent-input.json').read_text(encoding='utf-8'))
        body = {'schema_version': SCHEMA, 'binding': data['binding'], 'candidate_plan': None,
                'image_execution_plan': json.dumps(images), 'missing_inputs': []}
        calls.append((argv, prompt, kwargs, data))
        if mutate:
            value = mutate(body, schema_path, data)
            if isinstance(value, ReadonlyCodexResult): return value
            if value is not None: body = value
        return ReadonlyCodexResult(0, _events(body))
    monkeypatch.setattr('shared_platform.worker_category_readonly_cli.run_readonly_jsonl', run)
    return calls


def _execute_direct(engine, profile, worker, token):
    task = engine.get(next(iter(worker._task_ids)))
    with worker.boundary.hold(engine, task, token, profile):
        native.prepare_facts(task, profile, prepare_review=False)
        return worker.facts_adapter.execute_facts(task,
            profile.data_root / 'artifacts' / task['task_id'] / 'facts-1', [], lease_token=token)


def test_actual_worker_hold_typed_proposal_reaches_original_prepare_freeze_and_owner_reader(live, monkeypatch):
    engine, profile, worker, token, images = _leased(live, monkeypatch)
    calls = _child(monkeypatch, images)
    try:
        task = engine.get(next(iter(worker._task_ids)))
        worker.adapters['publication'](engine, task, token, profile)
        current = engine.get(task['task_id'])
        assert current['steps'][0]['state'] == 'completed', current
        frozen = native.read_frozen(current, profile)
        assert frozen and frozen == current['steps'][0]['checkpoint']['native_r1']
        assert current['current_step'] == 'images' and not current['required_action']
        assert len(calls) == 1 and len(live['fake'].calls) == 7
        argv, prompt, kwargs, _ = calls[0]
        assert argv[argv.index('--sandbox') + 1] == 'read-only' and '--add-dir' not in argv
        assert token not in prompt and kwargs['cwd'] == profile.root
        packet = json.loads((publication_rounds.report_dir(live['offer']) / 'first-review.json').read_text(encoding='utf-8'))
        assert packet['image_execution_plan'] == images
        assert packet['external_write_count'] == 0 and packet['readback_verified'] is False
        assert operations_runtime._R1_FILESYSTEM_BOUNDARY_VERIFIED is False
        with engine.transaction() as db:
            assert db.execute('SELECT COUNT(*) FROM workbench_category_agent_attempts').fetchone()[0] == 1
        snapshot = publication_rounds.report_dir(live['offer']) / 'round1-approved-snapshot.json'
        original = snapshot.read_bytes()
        with pytest.raises(ValueError):
            worker.facts_adapter.execute_facts(current,
                profile.data_root / 'artifacts' / task['task_id'] / 'facts-2', [], lease_token=token)
        assert snapshot.read_bytes() == original and len(calls) == 1
        # The real step producer retains this active lease for the next step;
        # a second claim here would be a fixture setup error.
        worker.adapters['publication'](engine, engine.get(current['task_id']), token, profile)
        images_pending = engine.get(current['task_id'])
        assert images_pending['execution_state'] == 'failed' and not images_pending['required_action']
        failures = [event for event in engine.store.events(current['task_id'])
                    if event['event_type'] == 'execution_failed']
        assert failures and failures[0]['detail']['state'] == 'failed'
        assert failures[0]['detail']['reason'] == 'R2_PAID_HISTORY_ROOT_BINDING_REQUIRED'
        assert len(calls) == 1 and len(live['fake'].calls) == 7
    finally: worker.close()


def test_unknown_child_is_not_relaunched_by_same_ledger_or_worker_checkpoint(live, monkeypatch):
    engine, profile, worker, token, images = _leased(live, monkeypatch)
    calls = _child(monkeypatch, images, lambda *_: ReadonlyCodexResult(None, '', timed_out=True))
    try:
        task = engine.get(next(iter(worker._task_ids)))
        with worker.boundary.hold(engine, task, token, profile):
            native.prepare_facts(task, profile, prepare_review=False)
            first = worker.facts_adapter.execute_facts(task,
                profile.data_root / 'artifacts' / task['task_id'] / 'facts-1', [], lease_token=token)
            repeated = worker.facts_adapter.execute_facts(task,
                profile.data_root / 'artifacts' / task['task_id'] / 'facts-2', [], lease_token=token)
        assert first['status'] == repeated['status'] == 'unknown' and len(calls) == 1
        worker.adapters['publication'](engine, task, token, profile)
        current = engine.get(task['task_id'])
        assert current['execution_state'] == 'reconciliation_required'
        assert current['checkpoint']['facts_attempt']['state'] == 'unknown'
        assert len(calls) == 1
        assert not (publication_rounds.report_dir(live['offer']) / 'first-review-image-plan.json').exists()
        assert not current['required_action']
    finally: worker.close()


@pytest.mark.parametrize('damage', ['binding', 'targets', 'extra-path', 'approval', 'image-status', 'inner-path', 'inner-duplicate', 'duplicate', 'oversize'])
def test_invalid_typed_result_is_unknown_without_parent_sidecar_or_freeze(live, monkeypatch, damage):
    engine, profile, worker, token, images = _leased(live, monkeypatch)
    def damage_result(body, *_):
        if damage == 'binding': body['binding']['offer_id'] = '123'
        elif damage == 'targets': body['binding']['targets'] = ['tiktok:MX']
        elif damage == 'extra-path': body['output_path'] = 'outside.json'
        elif damage == 'approval': body['approved_by'] = 'Kyle'
        elif damage == 'image-status':
            value=json.loads(body['image_execution_plan']);value['status']='APPROVED'
            body['image_execution_plan']=json.dumps(value)
        elif damage == 'inner-path':
            value=json.loads(body['image_execution_plan']);value['output_path']='outside.json'
            body['image_execution_plan']=json.dumps(value)
        elif damage == 'inner-duplicate':
            body['image_execution_plan']=body['image_execution_plan'][:-1]+',"status":"PROPOSED"}'
        elif damage == 'duplicate': return json.dumps(body)[:-1] + ',"schema_version":"' + SCHEMA + '"}'
        elif damage == 'oversize': body['missing_inputs'] = ['a' * (128 * 1024)]
    calls = _child(monkeypatch, images, damage_result)
    try:
        result = _execute_direct(engine, profile, worker, token)
        assert result['status'] == 'unknown' and len(calls) == 1
        directory = publication_rounds.report_dir(live['offer'])
        assert not (directory / 'first-review-image-plan.json').exists()
        assert not (directory / 'round1-approved-snapshot.json').exists()
        assert operations_runtime._R1_FILESYSTEM_BOUNDARY_VERIFIED is False
    finally: worker.close()


def test_revoked_trusted_capture_does_not_launch_or_reserve_a_child(live, monkeypatch):
    engine, profile, worker, token, images = _leased(live, monkeypatch)
    calls = _child(monkeypatch, images)
    try:
        with engine.transaction() as db:
            reference = db.execute("SELECT result_reference FROM workbench_category_intents WHERE action='capture'").fetchone()[0]
        live['store'].invalidate_round1_category_observation(reference, 'SOURCE_REVOKED')
        result = _execute_direct(engine, profile, worker, token)
        assert result['status'] == 'unknown' and result['reason'] == 'R1_PARENT_CAPTURE_REQUIRES_RECONCILIATION'
        with engine.transaction() as db:
            assert not db.execute("SELECT 1 FROM sqlite_master WHERE name='workbench_category_agent_attempts'").fetchone()
        assert not calls and not (publication_rounds.report_dir(live['offer']) / 'first-review-image-plan.json').exists()
    finally: worker.close()


@pytest.mark.parametrize('damage', ['hardlink', 'symlink'])
def test_child_replaced_artifact_is_not_consumed_and_outside_bytes_stay_unchanged(live, monkeypatch, damage):
    engine, profile, worker, token, images = _leased(live, monkeypatch)
    outside = live['root'] / 'outside-child-original'; outside.write_bytes(b'original')
    def replace_artifact(body, schema_path, data):
        path = schema_path.parent / 'parent-input.json'; path.unlink()
        if damage == 'hardlink': os.link(outside, path)
        else: path.symlink_to(outside)
    calls = _child(monkeypatch, images, replace_artifact)
    try:
        assert _execute_direct(engine, profile, worker, token)['status'] == 'unknown'
        assert len(calls) == 1 and outside.read_bytes() == b'original'
        assert not (publication_rounds.report_dir(live['offer']) / 'first-review-image-plan.json').exists()
    finally: worker.close()


def test_default_factory_rejects_calls_outside_hold_before_child_or_sidecar(live, monkeypatch):
    engine, profile, worker, token, images = _leased(live, monkeypatch)
    calls = _child(monkeypatch, images)
    try:
        task = engine.get(next(iter(worker._task_ids)))
        with pytest.raises(ValueError, match='R1_PARENT_FACTS_HOLD_REQUIRED'):
            worker.facts_adapter.execute_facts(task,
                profile.data_root / 'artifacts' / task['task_id'] / 'facts-1', [], lease_token=token)
        assert not calls and not (publication_rounds.report_dir(live['offer']) / 'first-review-image-plan.json').exists()
    finally: worker.close()


def test_current_source_drift_during_child_is_rebuilt_but_never_adopts_proposal(live, monkeypatch):
    from modules.sourcing import new_product_workbench as workbench
    engine, profile, worker, token, images = _leased(live, monkeypatch)
    def drift(body, *_):
        state = workbench.load_state(live['offer'])
        state['review']['title'] = 'Changed current product facts'
        workbench.save_state(live['offer'], state)
    calls = _child(monkeypatch, images, drift)
    try:
        assert _execute_direct(engine, profile, worker, token)['status'] == 'unknown'
        assert len(calls) == 1
        assert not (publication_rounds.report_dir(live['offer']) / 'first-review-image-plan.json').exists()
        assert not (publication_rounds.report_dir(live['offer']) / 'round1-approved-snapshot.json').exists()
    finally: worker.close()


def test_expired_original_lease_after_child_cannot_adopt_its_valid_proposal(live, monkeypatch):
    engine, profile, worker, token, images = _leased(live, monkeypatch)
    def expire(body, *_):
        with engine.transaction() as db:
            db.execute('UPDATE workbench_execution SET lease_until=0 WHERE task_id=?',
                       (body['binding']['task_id'],))
    calls = _child(monkeypatch, images, expire)
    try:
        assert _execute_direct(engine, profile, worker, token)['status'] == 'unknown'
        assert len(calls) == 1
        assert not (publication_rounds.report_dir(live['offer']) / 'first-review-image-plan.json').exists()
    finally: worker.close()
