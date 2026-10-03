"""Offline R1 facts handoff: only parent-verified capture reaches a fake agent."""

import json
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from shared_platform.operations_runtime import ControlledAgentBridge, RuntimeProfile
from shared_platform.worker_category_admission import request_id
from shared_platform.worker_category_facts_transport import VerifiedFactsCategoryTransport
from shared_platform.worker_category_intents import CategoryIntentLedger
from shared_platform.worker_category_readonly_cli import ReadonlyCodexResult
from test_round1_initial_options import choice
from test_worker_category_bridge import fixture, execute


@pytest.fixture(autouse=True)
def _synthetic_r1_filesystem_boundary(monkeypatch):
    # Exercise the internal transport with isolated fixtures; production R1
    # stays blocked until the directory boundary is proven race free.
    monkeypatch.setattr('shared_platform.operations_runtime._R1_FILESYSTEM_BOUNDARY_VERIFIED',
                        True)


def _agent_bridge(tmp_path, transport=None):
    profile = RuntimeProfile(tmp_path, tmp_path / 'operations', 'stable',
                             'a' * 40, 'pinned')
    return ControlledAgentBridge('fake-codex', profile,
                                 category_transport=transport)


def _fake_agent(monkeypatch):
    calls = []
    def run(argv, *, input, **kwargs):
        calls.append((argv, input, kwargs))
        return SimpleNamespace(returncode=0, stdout=_facts_events(),
                               overflow=False, timed_out=False)
    monkeypatch.setattr('shared_platform.operations_runtime.subprocess.run', run)
    monkeypatch.setattr('shared_platform.worker_category_readonly_cli.run_readonly_jsonl',
                        lambda argv, prompt, **kwargs: run(argv, input=prompt, **kwargs))
    return calls


def _facts_events(body=None):
    if body is None:
        body = {'summary': 'fake', 'missing_inputs': [], 'evidence_paths': []}
    return '\n'.join(json.dumps(event) for event in (
        {'type': 'thread.started', 'thread_id': 'fixture-facts-session'},
        {'type': 'turn.started'},
        {'type': 'item.completed', 'item': {'id': 'fixture-final',
            'type': 'agent_message', 'text': json.dumps(body)}},
        {'type': 'turn.completed', 'usage': {}})) + '\n'


def test_facts_without_private_transport_or_capture_never_starts_agent(tmp_path, monkeypatch):
    engine, task_id, lease, body, fake, store, category = fixture(tmp_path, monkeypatch)
    calls = _fake_agent(monkeypatch)
    task = engine.get(task_id)
    plain = _agent_bridge(tmp_path)
    assert plain.execute_facts(task, plain.profile.data_root / 'facts-plain', [],
                               lease_token=lease)['status'] == 'blocked'
    transport = VerifiedFactsCategoryTransport(engine, store, worker_id='worker')
    bridged = _agent_bridge(tmp_path, transport)
    assert bridged.execute_facts(task, bridged.profile.data_root / 'facts-uncaptured', [],
                                 lease_token=lease)['status'] == 'blocked'
    assert calls == [] and fake.calls == [] and not store.path.exists()


def test_parent_verified_capture_handed_to_fake_agent_without_url_or_lease(tmp_path, monkeypatch):
    engine, task_id, lease, body, fake, store, category = fixture(tmp_path, monkeypatch)
    options = execute(category, task_id, lease, 'options', body)
    selected = choice(body, options['domain_response'][1])
    selected['request_id'] = request_id(task_id, 'capture', selected)
    captured = execute(category, task_id, lease, 'capture', selected)
    assert captured['status'] == 'SUCCEEDED'
    calls = _fake_agent(monkeypatch)
    bridge = _agent_bridge(tmp_path, VerifiedFactsCategoryTransport(
        engine, store, worker_id='worker'))
    result = bridge.execute_facts(engine.get(task_id),
                                  bridge.profile.data_root / 'facts-verified', [],
                                  lease_token=lease)
    assert result['status'] == 'observed' and len(calls) == 1
    argv, prompt, _ = calls[0]
    assert '127.0.0.1' not in prompt and 'service_url' not in prompt
    assert lease not in prompt and 'options/capture APIs' not in prompt
    evidence = Path(result['category_evidence']['path'])
    packet = json.loads(evidence.read_text(encoding='utf-8'))
    assert packet['task_id'] == task_id
    assert packet['offer_id'] == body['offer_id']
    assert packet['capture_request_id'] == selected['request_id']
    assert packet['observer_reference'] == captured['result_reference']
    assert packet['observation']['observer_reference'] == captured['result_reference']
    assert len(fake.calls) == 7


@pytest.mark.parametrize('link_kind', ['symlink', 'junction'])
@pytest.mark.parametrize('directory', ['reports', 'state'])
def test_facts_never_grants_linked_project_directory(
        tmp_path, monkeypatch, link_kind, directory):
    if link_kind == 'junction' and os.name != 'nt':
        pytest.skip('Windows junction only')
    engine, task_id, lease, body, fake, store, category = fixture(tmp_path, monkeypatch)
    options = execute(category, task_id, lease, 'options', body)
    selected = choice(body, options['domain_response'][1])
    selected['request_id'] = request_id(task_id, 'capture', selected)
    assert execute(category, task_id, lease, 'capture', selected)['status'] == 'SUCCEEDED'
    outside = tmp_path / ('outside-' + directory)
    outside.mkdir()
    sentinel = outside / 'sentinel.txt'
    sentinel.write_text('untouched', encoding='utf-8')
    linked = (tmp_path / 'reports/product-preparation' / body['offer_id']
              if directory == 'reports' else tmp_path / 'data/new_product_workbench')
    linked.parent.mkdir(parents=True, exist_ok=True)
    if link_kind == 'symlink':
        linked.symlink_to(outside, target_is_directory=True)
    else:
        subprocess.run(['cmd.exe', '/d', '/c', 'mklink', '/J',
                        str(linked), str(outside)], check=True, capture_output=True)
    assert linked.resolve() == outside.resolve()

    calls = []
    def run(argv, *, input, **kwargs):
        calls.append((argv, input, kwargs))
        for index, argument in enumerate(argv[:-1]):
            if argument == '--add-dir':
                Path(argv[index + 1], 'sentinel.txt').write_text(
                    'outside write via grant', encoding='utf-8')
        return SimpleNamespace(returncode=0, stdout=_facts_events(),
                               overflow=False, timed_out=False)
    monkeypatch.setattr('shared_platform.operations_runtime.subprocess.run', run)
    monkeypatch.setattr('shared_platform.worker_category_readonly_cli.run_readonly_jsonl',
                        lambda argv, prompt, **kwargs: run(argv, input=prompt, **kwargs))
    bridge = _agent_bridge(tmp_path, VerifiedFactsCategoryTransport(
        engine, store, worker_id='worker'))
    output = bridge.profile.data_root / 'facts-linked'
    result = bridge.execute_facts(engine.get(task_id), output, [], lease_token=lease)
    assert result['status'] == 'observed' and len(calls) == 1
    argv, prompt, kwargs = calls[0]
    assert '--add-dir' not in argv
    assert kwargs['cwd'] == bridge.profile.root
    assert 'Do not create or modify sidecars' in prompt
    assert sentinel.read_text(encoding='utf-8') == 'untouched'


@pytest.mark.parametrize('directory', ['reports', 'state'])
def test_facts_directory_swapped_at_child_launch_is_not_granted(
        tmp_path, monkeypatch, directory):
    engine, task_id, lease, body, fake, store, category = fixture(tmp_path, monkeypatch)
    options = execute(category, task_id, lease, 'options', body)
    selected = choice(body, options['domain_response'][1])
    selected['request_id'] = request_id(task_id, 'capture', selected)
    assert execute(category, task_id, lease, 'capture', selected)['status'] == 'SUCCEEDED'
    outside = tmp_path / ('outside-launch-' + directory)
    outside.mkdir()
    sentinel = outside / 'sentinel.txt'
    sentinel.write_text('untouched', encoding='utf-8')
    linked = (tmp_path / 'reports/product-preparation' / body['offer_id']
              if directory == 'reports' else tmp_path / 'data/new_product_workbench')
    linked.mkdir(parents=True)
    calls = []
    def run(argv, *, input, **kwargs):
        linked.rmdir()
        linked.symlink_to(outside, target_is_directory=True)
        calls.append(argv)
        for index, argument in enumerate(argv[:-1]):
            if argument == '--add-dir':
                Path(argv[index + 1], 'sentinel.txt').write_text(
                    'outside write via launch race', encoding='utf-8')
        return SimpleNamespace(returncode=0, stdout=_facts_events(),
                               overflow=False, timed_out=False)
    monkeypatch.setattr('shared_platform.operations_runtime.subprocess.run', run)
    monkeypatch.setattr('shared_platform.worker_category_readonly_cli.run_readonly_jsonl',
                        lambda argv, prompt, **kwargs: run(argv, input=prompt, **kwargs))
    bridge = _agent_bridge(tmp_path, VerifiedFactsCategoryTransport(
        engine, store, worker_id='worker'))
    result = bridge.execute_facts(engine.get(task_id),
        bridge.profile.data_root / 'launch-swap', [], lease_token=lease)
    assert result['status'] == 'observed'
    assert len(calls) == 1 and '--add-dir' not in calls[0]
    assert linked.resolve() == outside.resolve()
    assert sentinel.read_text(encoding='utf-8') == 'untouched'


@pytest.mark.parametrize('invalid', [
    {'summary': None, 'missing_inputs': [], 'evidence_paths': []},
    {'summary': 1, 'missing_inputs': [], 'evidence_paths': []},
    {'summary': 'ok', 'missing_inputs': 'none', 'evidence_paths': []},
    {'summary': 'ok', 'missing_inputs': ['ok', 2], 'evidence_paths': []},
    {'summary': 'ok', 'missing_inputs': [], 'evidence_paths': 'none'},
    {'summary': 'ok', 'missing_inputs': [], 'evidence_paths': ['ok', None]},
])
def test_facts_invalid_result_types_are_unknown(tmp_path, monkeypatch, invalid):
    engine, task_id, lease, body, fake, store, category = fixture(tmp_path, monkeypatch)
    options = execute(category, task_id, lease, 'options', body)
    selected = choice(body, options['domain_response'][1])
    selected['request_id'] = request_id(task_id, 'capture', selected)
    assert execute(category, task_id, lease, 'capture', selected)['status'] == 'SUCCEEDED'
    calls = []
    def run(argv, *, input, **kwargs):
        calls.append(argv)
        return SimpleNamespace(returncode=0, stdout=_facts_events(invalid),
                               overflow=False, timed_out=False)
    monkeypatch.setattr('shared_platform.operations_runtime.subprocess.run', run)
    monkeypatch.setattr('shared_platform.worker_category_readonly_cli.run_readonly_jsonl',
                        lambda argv, prompt, **kwargs: run(argv, input=prompt, **kwargs))
    bridge = _agent_bridge(tmp_path, VerifiedFactsCategoryTransport(
        engine, store, worker_id='worker'))
    result = bridge.execute_facts(engine.get(task_id),
        bridge.profile.data_root / 'invalid-types', [], lease_token=lease)
    assert result['status'] == 'unknown'
    assert result['reason'] == 'category_facts_result_invalid'
    assert len(calls) == 1


def test_wrong_lease_and_old_human_request_cannot_be_handed_to_agent(tmp_path, monkeypatch):
    engine, task_id, lease, body, fake, store, category = fixture(tmp_path, monkeypatch)
    calls = _fake_agent(monkeypatch)
    bridge = _agent_bridge(tmp_path, VerifiedFactsCategoryTransport(
        engine, store, worker_id='worker'))
    assert bridge.execute_facts(engine.get(task_id), bridge.profile.data_root / 'bad-lease', [],
                                lease_token='forged')['status'] == 'blocked'
    assert calls == []
    # A human-origin request, even with the same deterministic ID, is not a
    # trusted task-origin capture and cannot be converted into agent evidence.
    from test_round1_category_observer_http import request
    assert request('options', body)[0] == 200
    assert bridge.execute_facts(engine.get(task_id), bridge.profile.data_root / 'human', [],
                                lease_token=lease)['status'] == 'blocked'
    assert calls == []


def test_unknown_capture_and_release_drift_never_start_agent(tmp_path, monkeypatch):
    engine, task_id, lease, body, fake, store, category = fixture(tmp_path, monkeypatch)
    calls = _fake_agent(monkeypatch)
    bridge = _agent_bridge(tmp_path, VerifiedFactsCategoryTransport(
        engine, store, worker_id='worker'))
    stale = engine.get(task_id)
    stale['version'] = dict(stale['version'], code_version='b' * 40)
    assert bridge.execute_facts(stale, bridge.profile.data_root / 'stale', [],
                                lease_token=lease)['status'] == 'blocked'
    # A reserved request without a domain commit is UNKNOWN. It is never
    # treated as capture evidence or re-dispatched by facts preparation.
    CategoryIntentLedger().reserve(engine, task_id=task_id, worker_id='worker',
                                   lease_token=lease, action='options', body=body)
    assert bridge.execute_facts(engine.get(task_id), bridge.profile.data_root / 'unknown', [],
                                lease_token=lease)['status'] == 'blocked'
    assert calls == [] and fake.calls == [] and not store.path.exists()


def test_fake_agent_cannot_change_frozen_capture_evidence(tmp_path, monkeypatch):
    engine, task_id, lease, body, fake, store, category = fixture(tmp_path, monkeypatch)
    options = execute(category, task_id, lease, 'options', body)
    selected = choice(body, options['domain_response'][1])
    selected['request_id'] = request_id(task_id, 'capture', selected)
    assert execute(category, task_id, lease, 'capture', selected)['status'] == 'SUCCEEDED'
    def tamper(argv, *, input, **kwargs):
        evidence = Path(argv[argv.index('--output-schema') + 1]).parent / 'trusted-r1-category-capture.json'
        evidence.write_text('{}', encoding='utf-8')
        return SimpleNamespace(returncode=0, stdout=_facts_events(),
                               overflow=False, timed_out=False)
    monkeypatch.setattr('shared_platform.operations_runtime.subprocess.run', tamper)
    monkeypatch.setattr('shared_platform.worker_category_readonly_cli.run_readonly_jsonl',
                        lambda argv, prompt, **kwargs: tamper(argv, input=prompt, **kwargs))
    bridge = _agent_bridge(tmp_path, VerifiedFactsCategoryTransport(
        engine, store, worker_id='worker'))
    result = bridge.execute_facts(engine.get(task_id),
                                  bridge.profile.data_root / 'tampered', [],
                                  lease_token=lease)
    assert result == {'status': 'blocked',
                      'reason': 'trusted_r1_capture_evidence_changed'}


@pytest.mark.parametrize('overflow,timed_out,reason', [
    (True, False, 'category_facts_stdout_limit_exceeded'),
    (False, True, 'category_facts_timeout_reconcile_session'),
])
def test_bounded_facts_child_never_relaunches_after_incomplete_transport(
        tmp_path, monkeypatch, overflow, timed_out, reason):
    engine, task_id, lease, body, fake, store, category = fixture(tmp_path, monkeypatch)
    options = execute(category, task_id, lease, 'options', body)
    selected = choice(body, options['domain_response'][1])
    selected['request_id'] = request_id(task_id, 'capture', selected)
    assert execute(category, task_id, lease, 'capture', selected)['status'] == 'SUCCEEDED'
    calls = []
    def bounded(argv, prompt, **kwargs):
        calls.append((argv, prompt, kwargs))
        return ReadonlyCodexResult(1, _facts_events(), overflow, timed_out)
    monkeypatch.setattr('shared_platform.worker_category_readonly_cli.run_readonly_jsonl', bounded)
    bridge = _agent_bridge(tmp_path, VerifiedFactsCategoryTransport(
        engine, store, worker_id='worker'))
    first = bridge.execute_facts(engine.get(task_id),
                                 bridge.profile.data_root / 'bounded', [],
                                 lease_token=lease)
    assert first['status'] == 'unknown' and first['reason'] == reason
    assert len(calls) == 1
    retry = bridge.execute_facts(engine.get(task_id),
                                 bridge.profile.data_root / 'retry', [],
                                 lease_token=lease)
    assert retry['reason'] == 'category_facts_attempt_exists_reconcile'
    assert len(calls) == 1
