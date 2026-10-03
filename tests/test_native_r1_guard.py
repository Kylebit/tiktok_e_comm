"""Synthetic R1 lineage guards; no worker, provider, or real Offer state."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json

import pytest

from modules.sourcing import new_product_workbench as workbench
from modules.products import server
from shared_platform import operations_publication, publication_rounds, round1_workspace
from shared_platform import workbench_publication_native as native
from shared_platform.workbench_engine import WorkbenchEngine
from test_round1_workspace_freeze import live, prepared
from test_round1_auto_freeze import complete_source, public_settings, request


def _profile(root):
    return SimpleNamespace(root=root, data_root=root / 'data/operations/stable',
                           version='fixture-r1-release',
                           environment='preview', manifest_digest='fixture-manifest')


def _task(offer, targets, reference, profile):
    release = {'code_version': profile.version, 'environment': profile.environment,
               'manifest_digest': profile.manifest_digest}
    engine = WorkbenchEngine(profile.data_root / 'tasks.db', release)
    engine.register_executor('guard-worker', ['publication'], engine.release)
    task = engine.create({'template': 'publication', 'source_key': 'original-' + offer,
                          'scope': {'offer_id': offer, 'shops': targets}})
    token = engine.claim(task['task_id'], 'guard-worker')['lease_token']
    engine.record_checkpoint(task['task_id'], token,
                             {'native_preparation': {'prepared_reference': reference}})
    engine.wait_for_user(task['task_id'], token, kind='review', label='原首轮审核',
        reason='synthetic original task', receipt_binding={'adapter': 'publication-native/v1',
        'step': 'facts', 'offer_id': offer, 'targets': targets,
        'prepared_reference': reference})
    return engine.get(task['task_id'])


def test_original_human_task_reads_its_snapshot_but_new_task_cannot_adopt(live):
    packet, _ = prepared(live)
    offer = live['offer']
    profile = _profile(live['root'])
    targets = sorted(packet['packet']['target_selection']['requested'])
    original = _task(offer, targets, packet['prepared_reference'], profile)
    code, approved = live['call']('approve', {'offer_id': offer,
        'prepared_reference': packet['prepared_reference'], 'user_approved': True,
        'approved_by': packet['approval_actor']})
    assert code == 200 and approved['status'] == 'FROZEN'
    frozen = native.read_frozen(original, profile)
    assert frozen['snapshot_digest'] == approved['snapshot']['snapshot_digest']
    assert workbench.load_state(offer)['product_approval']['approved_by'] == 'Kyle'

    new_task = {**deepcopy(original), 'task_id': 'TASK-new', 'checkpoint': {},
                'required_action': None}
    with pytest.raises(native.NativeR1ReconciliationRequired, match='NATIVE_R1_TASK_ORIGIN_UNAVAILABLE'):
        native.read_frozen(new_task, profile)
    forged_action = {**new_task, 'required_action': original['required_action']}
    with pytest.raises(native.NativeR1ReconciliationRequired, match='NATIVE_R1_TASK_ORIGIN_UNAVAILABLE'):
        native.read_frozen(forged_action, profile)
    engine = WorkbenchEngine(profile.data_root / 'tasks.db', original['version'])
    engine.register_executor('guard-worker', ['publication'], engine.release)
    second = engine.create({'template': 'publication', 'source_key': 'second-' + offer,
                            'scope': {'offer_id': offer, 'shops': targets}})
    copied = {**engine.get(second['task_id']), 'checkpoint': original['checkpoint'],
              'required_action': original['required_action']}
    with pytest.raises(native.NativeR1ReconciliationRequired, match='NATIVE_R1_TASK_LEDGER_CONFLICT'):
        native.read_frozen(copied, profile)
    # Even a later task with its own durable checkpoint for the same reference
    # cannot become the original owner of the already approved snapshot.
    with engine.transaction() as conn:
        conn.execute('UPDATE workbench_execution SET checkpoint_json=? WHERE task_id=?',
                     (json.dumps(original['checkpoint']), second['task_id']))
        engine._event(conn, second['task_id'], 'checkpoint_saved',
                      {'checkpoint': original['checkpoint']})
    with pytest.raises(native.NativeR1ReconciliationRequired,
                       match='NATIVE_R1_ORIGINAL_TASK_OWNER_CONFLICT'):
        native.read_frozen(engine.get(second['task_id']), profile)
    wrong_release = deepcopy(original)
    wrong_release['version']['code_version'] = 'later-release'
    with pytest.raises(native.NativeR1ReconciliationRequired, match='NATIVE_R1_TASK_RELEASE_OR_REFERENCE_REQUIRED'):
        native.read_frozen(wrong_release, profile)
    wrong_action = deepcopy(original)
    wrong_action['required_action']['receipt_binding']['prepared_reference'] = 'r1-prepared:other'
    with pytest.raises(native.NativeR1ReconciliationRequired, match='NATIVE_R1_TASK_LEDGER_CONFLICT'):
        native.read_frozen(wrong_action, profile)

    engine.accept_domain_receipt(original['task_id'], {'receipt_id': 'fixture-human',
        'native_r1': frozen}, lambda *_: True)
    token = engine.claim(original['task_id'], 'guard-worker')['lease_token']
    engine.complete_step(original['task_id'], token, expected_step='facts',
                         checkpoint={'native_r1': frozen})
    completed = engine.get(original['task_id'])
    assert native.read_frozen(completed, profile) == frozen


def test_new_task_cannot_adopt_old_technical_snapshot(live):
    complete_source(live)
    packet, _ = prepared(live)
    profile = _profile(live['root'])
    targets = sorted(packet['packet']['target_selection']['requested'])
    original = _task(live['offer'], targets, packet['prepared_reference'], profile)
    frozen = round1_workspace.auto_freeze(server, request(packet, live['offer']))
    assert frozen['status'] == 'FROZEN' and frozen['human_approval'] is False
    assert native.read_frozen(original, profile)['snapshot_digest'] == frozen['snapshot']['snapshot_digest']
    new_task = {**deepcopy(original), 'task_id': 'TASK-new', 'checkpoint': {},
                'required_action': None}
    with pytest.raises(native.NativeR1ReconciliationRequired, match='NATIVE_R1_TASK_ORIGIN_UNAVAILABLE'):
        native.read_frozen(new_task, profile)


@pytest.mark.parametrize('durable_mark', ['decision', 'technical_cas'])
def test_waiting_first_review_reconciles_technical_progress_without_snapshot(live, monkeypatch, durable_mark):
    complete_source(live)
    packet, _ = prepared(live)
    profile = _profile(live['root'])
    targets = sorted(packet['packet']['target_selection']['requested'])
    waiting = _task(live['offer'], targets, packet['prepared_reference'], profile)
    engine = WorkbenchEngine(profile.data_root / 'tasks.db', waiting['version'])
    if durable_mark == 'decision':
        monkeypatch.setattr(workbench, 'save_state', lambda *_: (_ for _ in ()).throw(
            OSError('synthetic crash before technical CAS')))
        failure = 'R1_AUTO_STATE_NOT_SAVED'
    else:
        from shared_platform import immutable_approval_files
        original = immutable_approval_files.persist_immutable_bytes

        def fail_snapshot(path, content, *, root):
            if path.name == 'round1-approved-snapshot.json':
                raise OSError('synthetic crash after technical CAS')
            return original(path, content, root=root)

        monkeypatch.setattr(immutable_approval_files, 'persist_immutable_bytes', fail_snapshot)
        failure = 'R1_AUTO_APPROVED_NOT_FROZEN'
    with pytest.raises(round1_workspace.Round1WorkspaceError, match=failure):
        round1_workspace.auto_freeze(server, request(packet, live['offer']))
    assert native.observe(engine, engine.get(waiting['task_id']), profile) is True
    observed = engine.get(waiting['task_id'])
    assert observed['execution_state'] == 'reconciliation_required'
    assert observed['current_step'] == 'facts' and not observed['required_action']
    assert not (live['root'] / 'reports/product-preparation' / live['offer'] /
                'round1-approved-snapshot.json').exists()


@pytest.mark.parametrize('durable_mark', ['decision', 'technical_cas'])
def test_unfinished_technical_freeze_cannot_overwrite_first_review(tmp_path, monkeypatch, durable_mark):
    offer = '12345'
    directory = tmp_path / 'reports/product-preparation' / offer
    directory.mkdir(parents=True)
    review_path = directory / 'first-review.json'
    original = json.dumps({'offer_id': offer, 'marker': 'original immutable source'}).encode()
    review_path.write_bytes(original)
    monkeypatch.setattr(native, '_server', lambda _: object())
    monkeypatch.setattr(native, '_report_dir', lambda *_: directory)
    if durable_mark == 'decision':
        (directory / 'round1-auto-decision.json').write_text('{}', encoding='utf-8')
        approval = {}
    else:
        approval = {'status': 'approved', 'approved_by': publication_rounds.AUTOPILOT_ACTOR}
    monkeypatch.setattr(workbench, 'load_state', lambda _: {'_revision': 8, 'product_approval': approval})
    task = {'scope': {'offer_id': offer, 'shops': ['shopee:MY']}}
    with pytest.raises(native.NativeR1ReconciliationRequired, match='NATIVE_R1_TECHNICAL_FREEZE_REQUIRES_RECONCILIATION'):
        native._prepare_facts_locked(task, SimpleNamespace(root=tmp_path), preview_builder=lambda _: {})
    assert review_path.read_bytes() == original
    assert not (directory / 'round1-approved-snapshot.json').exists()


def test_task_adapter_records_reconciliation_without_review_or_input(tmp_path, monkeypatch):
    engine = WorkbenchEngine(tmp_path / 'tasks.db', {'code_version': 'fixture'})
    engine.register_executor('worker', ['publication'], engine.release)
    task = engine.create({'template': 'publication', 'source_key': 'guard-fixture',
                          'scope': {'offer_id': '123', 'shops': ['shopee:MY']}})
    token = engine.claim(task['task_id'], 'worker')['lease_token']
    monkeypatch.setattr(native, 'read_frozen', lambda *_: None)
    from shared_platform import operations_publication_prepare as prepare
    monkeypatch.setattr(prepare, '_current', lambda *_: (_ for _ in ()).throw(
        native.NativeR1ReconciliationRequired('NATIVE_R1_TECHNICAL_FREEZE_REQUIRES_RECONCILIATION')))
    run, _ = operations_publication.bindings()
    run(engine, engine.get(task['task_id']), token, SimpleNamespace(root=tmp_path))
    observed = engine.get(task['task_id'])
    assert observed['execution_state'] == 'reconciliation_required'
    assert observed['current_step'] == 'facts' and not observed['required_action']
