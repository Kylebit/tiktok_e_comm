"""Offline domain tests for policy-owned R1 freezing; no marketplace writes."""
from copy import deepcopy
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from modules.products import server
from modules.sourcing import new_product_workbench as workbench
from shared_platform import publication_autopilot, publication_rounds, round1_workspace
from shared_platform import workbench_publication_native
from shared_platform.workbench_engine import WorkbenchEngine
from test_round1_workspace_freeze import live, prepared


@pytest.fixture(autouse=True)
def public_settings(monkeypatch):
    # A new worktree has no private settings.json; the fixture uses only public
    # sample exchange rates and never needs a credential.
    from core import config
    sample = Path(__file__).resolve().parents[1] / 'config/settings.example.json'
    monkeypatch.setattr(config, '_cache', json.loads(sample.read_text(encoding='utf-8')))


def request(packet, offer):
    policy = publication_autopilot.load_autopilot_policy()
    return {'offer_id': offer, 'prepared_reference': packet['prepared_reference'],
            'policy_digest': publication_rounds.canonical_digest(policy)}


def complete_source(live):
    # Supply exact synthetic SKU price evidence absent from the older manual
    # approval fixture. Auto adoption must not waive that warning.
    path = live['root'] / 'data/new_product_workbench' / f"{live['offer']}.json"
    state = json.loads(path.read_text(encoding='utf-8'))
    state['source'] = {'skus': [{'key': 'size-large', 'name': 'Large', 'price': 4.4}]}
    path.write_text(json.dumps(state), encoding='utf-8')


def test_auto_freeze_uses_policy_actor_and_replays_without_second_state_write(live, monkeypatch):
    complete_source(live)
    packet, _ = prepared(live)
    data = request(packet, live['offer'])
    profile = SimpleNamespace(root=live['root'], data_root=live['root'] / 'data/operations/stable',
                              version='fixture-r1-release', environment='preview',
                              manifest_digest='fixture-manifest')
    engine = WorkbenchEngine(profile.data_root / 'tasks.db', {
        'code_version': profile.version, 'environment': profile.environment,
        'manifest_digest': profile.manifest_digest})
    engine.register_executor('guard-worker', ['publication'], engine.release)
    task = engine.create({'template': 'publication', 'source_key': 'original-' + live['offer'],
                          'scope': {'offer_id': live['offer'],
                                    'shops': packet['packet']['target_selection']['requested']}})
    token = engine.claim(task['task_id'], 'guard-worker')['lease_token']
    engine.record_checkpoint(task['task_id'], token, {
        'native_preparation': {'prepared_reference': packet['prepared_reference']}})
    original = workbench.save_state
    writes = []
    def save(*args, **kwargs):
        writes.append(args[0])
        return original(*args, **kwargs)
    monkeypatch.setattr(workbench, 'save_state', save)
    result = round1_workspace.auto_freeze(server, data)
    assert result['status'] == 'FROZEN' and result['human_approval'] is False
    assert result['external_write_count'] == 0
    state = workbench.load_state(live['offer'])
    approval = state['product_approval']
    assert approval['approved_by'] == publication_rounds.AUTOPILOT_ACTOR
    assert approval['approval_authority'] == 'ACTIVE_AUTOPILOT_POLICY'
    assert 'user_approved' not in approval
    assert result['snapshot']['human_approval'] is False
    assert publication_rounds.validate_round2_input(live['offer'], state) == result['snapshot']
    observed = round1_workspace.status(server, live['offer'], reference=data['prepared_reference'])
    assert observed['status'] == 'FROZEN' and observed['snapshot'] == result['snapshot']
    assert workbench_publication_native.read_frozen(
        engine.get(task['task_id']), profile)['snapshot_digest'] == result['snapshot']['snapshot_digest']
    assert round1_workspace.auto_freeze(server, data) == result
    assert writes == [live['offer']]


def test_auto_freeze_rejects_stale_policy_or_state_before_writing(live):
    complete_source(live)
    packet, _ = prepared(live)
    data = request(packet, live['offer'])
    with pytest.raises(round1_workspace.Round1WorkspaceError, match='R1_AUTO_POLICY_CHANGED'):
        round1_workspace.auto_freeze(server, {**data, 'policy_digest': 'sha256:' + '0' * 64})
    state = workbench.load_state(live['offer'])
    state['review']['title'] = 'Changed before technical freeze'
    workbench.save_state(live['offer'], state)
    with pytest.raises(round1_workspace.Round1WorkspaceError, match='R1_'):
        round1_workspace.auto_freeze(server, data)
    directory = publication_rounds.report_dir(live['offer'])
    assert not (directory / 'round1-auto-decision.json').exists()
    assert not (directory / 'round1-approved-snapshot.json').exists()


def test_auto_freeze_recovers_snapshot_failure_without_rewriting_state(live, monkeypatch):
    complete_source(live)
    packet, _ = prepared(live)
    data = request(packet, live['offer'])
    from shared_platform import immutable_approval_files
    original = immutable_approval_files.persist_immutable_bytes
    def fail_snapshot(path, content, *, root):
        if path.name == 'round1-approved-snapshot.json':
            raise OSError('synthetic disk failure')
        return original(path, content, root=root)
    monkeypatch.setattr(immutable_approval_files, 'persist_immutable_bytes', fail_snapshot)
    # Import binding is local to the domain function; failure must leave a
    # recognizable technical approval without claiming a frozen R1 snapshot.
    with pytest.raises(round1_workspace.Round1WorkspaceError, match='R1_AUTO_APPROVED_NOT_FROZEN'):
        round1_workspace.auto_freeze(server, data)
    before = workbench.load_state(live['offer'])
    assert before['product_approval']['approved_by'] == publication_rounds.AUTOPILOT_ACTOR
    assert not (publication_rounds.report_dir(live['offer']) / 'round1-approved-snapshot.json').exists()
    monkeypatch.setattr(immutable_approval_files, 'persist_immutable_bytes', original)
    monkeypatch.setattr(workbench, 'save_state', lambda *_: pytest.fail('state must not be written twice'))
    recovered = round1_workspace.auto_freeze(server, data)
    assert recovered['status'] == 'FROZEN'
    assert workbench.load_state(live['offer']) == before


def test_auto_freeze_rejects_existing_human_approval(live):
    complete_source(live)
    packet, _ = prepared(live)
    data = request(packet, live['offer'])
    code, result = live['call']('approve', {
        'offer_id': live['offer'], 'prepared_reference': packet['prepared_reference'],
        'user_approved': True, 'approved_by': packet['approval_actor']})
    assert code == 200 and result['status'] == 'FROZEN'
    with pytest.raises(round1_workspace.Round1WorkspaceError, match='R1_EXISTING_SNAPSHOT_CONFLICT'):
        round1_workspace.auto_freeze(server, data)


def test_auto_freeze_does_not_accept_missing_source_price_warning(live):
    packet, _ = prepared(live)
    data = request(packet, live['offer'])
    with pytest.raises(round1_workspace.Round1WorkspaceError, match='R1_AUTO_MATERIAL_INCOMPLETE'):
        round1_workspace.auto_freeze(server, data)
    assert not workbench.load_state(live['offer']).get('product_approval')
    assert not (publication_rounds.report_dir(live['offer']) / 'round1-auto-decision.json').exists()


def test_auto_freeze_resumes_durable_decision_after_state_save_failure(live, monkeypatch):
    complete_source(live)
    packet, _ = prepared(live)
    data = request(packet, live['offer'])
    original = workbench.save_state
    monkeypatch.setattr(workbench, 'save_state', lambda *_: (_ for _ in ()).throw(RuntimeError('synthetic CAS failure')))
    with pytest.raises(round1_workspace.Round1WorkspaceError, match='R1_AUTO_STATE_NOT_SAVED'):
        round1_workspace.auto_freeze(server, data)
    decision_path = publication_rounds.report_dir(live['offer']) / 'round1-auto-decision.json'
    decision_bytes = decision_path.read_bytes()
    assert not workbench.load_state(live['offer']).get('product_approval')
    monkeypatch.setattr(workbench, 'save_state', original)
    assert round1_workspace.auto_freeze(server, data)['status'] == 'FROZEN'
    assert decision_path.read_bytes() == decision_bytes


def test_auto_freeze_changes_only_the_exact_offer_state(live, monkeypatch):
    complete_source(live)
    packet, _ = prepared(live)
    monkeypatch.setattr(workbench, '_mirror_content_state_to_collect_box_owner',
                        lambda *_: pytest.fail('commercial freeze must not mirror content to another offer'))
    assert round1_workspace.auto_freeze(server, request(packet, live['offer']))['status'] == 'FROZEN'


def test_auto_freeze_reconciles_lost_state_save_response(live, monkeypatch):
    complete_source(live)
    packet, _ = prepared(live)
    original = workbench.save_state
    writes = []
    def lost_reply(*args, **kwargs):
        writes.append(args[0])
        original(*args, **kwargs)
        raise TimeoutError('synthetic return lost after durable CAS')
    monkeypatch.setattr(workbench, 'save_state', lost_reply)
    result = round1_workspace.auto_freeze(server, request(packet, live['offer']))
    assert result['status'] == 'FROZEN' and writes == [live['offer']]
    assert publication_rounds.validate_round2_input(live['offer'], workbench.load_state(live['offer'])) == result['snapshot']


def test_auto_freeze_recovers_committed_decision_after_policy_drift(live, monkeypatch):
    complete_source(live)
    packet, _ = prepared(live)
    data = request(packet, live['offer'])
    from shared_platform import immutable_approval_files
    original = immutable_approval_files.persist_immutable_bytes

    def fail_snapshot(path, content, *, root):
        if path.name == 'round1-approved-snapshot.json':
            raise OSError('synthetic crash after Product Center CAS')
        return original(path, content, root=root)

    monkeypatch.setattr(immutable_approval_files, 'persist_immutable_bytes', fail_snapshot)
    with pytest.raises(round1_workspace.Round1WorkspaceError, match='R1_AUTO_APPROVED_NOT_FROZEN'):
        round1_workspace.auto_freeze(server, data)
    monkeypatch.setattr(immutable_approval_files, 'persist_immutable_bytes', original)
    before = workbench.load_state(live['offer'])
    directory = publication_rounds.report_dir(live['offer'])
    decision_bytes = (directory / 'round1-auto-decision.json').read_bytes()
    assert round1_workspace.status(server, live['offer'], data['prepared_reference'])['status'] == 'APPROVED_NOT_FROZEN'
    assert not round1_workspace.status(server, live['offer'], data['prepared_reference'])['ok']

    changed_policy = deepcopy(publication_autopilot.load_autopilot_policy())
    changed_policy['policy_id'] += '-next'
    monkeypatch.setattr(publication_autopilot, 'load_autopilot_policy', lambda: changed_policy)
    monkeypatch.setattr(workbench, 'save_state', lambda *_args, **_kwargs: pytest.fail('CAS must not repeat'))
    recovered = round1_workspace.auto_freeze(server, data)
    assert recovered['status'] == 'FROZEN' and recovered['human_approval'] is False
    assert workbench.load_state(live['offer']) == before
    assert (directory / 'round1-auto-decision.json').read_bytes() == decision_bytes
    assert publication_rounds.validate_round2_input(live['offer'], before) == recovered['snapshot']
    assert round1_workspace.auto_freeze(server, data) == recovered


def test_auto_freeze_refuses_uncommitted_old_decision_after_policy_drift(live, monkeypatch):
    complete_source(live)
    packet, _ = prepared(live)
    data = request(packet, live['offer'])
    original = workbench.save_state
    monkeypatch.setattr(workbench, 'save_state', lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError('synthetic pre-CAS failure')))
    with pytest.raises(round1_workspace.Round1WorkspaceError, match='R1_AUTO_STATE_NOT_SAVED'):
        round1_workspace.auto_freeze(server, data)
    monkeypatch.setattr(workbench, 'save_state', original)
    directory = publication_rounds.report_dir(live['offer'])
    assert (directory / 'round1-auto-decision.json').is_file()
    assert not workbench.load_state(live['offer']).get('product_approval')

    changed_policy = deepcopy(publication_autopilot.load_autopilot_policy())
    changed_policy['policy_id'] += '-next'
    monkeypatch.setattr(publication_autopilot, 'load_autopilot_policy', lambda: changed_policy)
    with pytest.raises(round1_workspace.Round1WorkspaceError, match='R1_AUTO_POLICY_CHANGED'):
        round1_workspace.auto_freeze(server, data)
    assert not workbench.load_state(live['offer']).get('product_approval')
    assert not (directory / 'round1-approved-snapshot.json').exists()


def test_auto_freeze_flags_reconciliation_when_committed_source_drifts(live, monkeypatch):
    complete_source(live)
    packet, _ = prepared(live)
    data = request(packet, live['offer'])
    from shared_platform import immutable_approval_files
    original = immutable_approval_files.persist_immutable_bytes

    def fail_snapshot(path, content, *, root):
        if path.name == 'round1-approved-snapshot.json':
            raise OSError('synthetic crash after Product Center CAS')
        return original(path, content, root=root)

    monkeypatch.setattr(immutable_approval_files, 'persist_immutable_bytes', fail_snapshot)
    with pytest.raises(round1_workspace.Round1WorkspaceError, match='R1_AUTO_APPROVED_NOT_FROZEN'):
        round1_workspace.auto_freeze(server, data)
    monkeypatch.setattr(immutable_approval_files, 'persist_immutable_bytes', original)

    state = workbench.load_state(live['offer'])
    state['review']['title'] = 'Changed after technical CAS'
    workbench.save_state(live['offer'], state)
    observed = round1_workspace.status(server, live['offer'], data['prepared_reference'])
    assert observed['status'] == 'RECONCILIATION_REQUIRED' and observed['ok'] is False
    with pytest.raises(round1_workspace.Round1WorkspaceError, match='R1_AUTO_STATE_CONFLICT'):
        round1_workspace.auto_freeze(server, data)
    assert not (publication_rounds.report_dir(live['offer']) / 'round1-approved-snapshot.json').exists()
