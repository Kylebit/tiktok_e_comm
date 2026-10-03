"""All profile paths are synthetic; spy stops before any SQLite access."""
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from shared_platform import operations_domain_guard as guard


@pytest.fixture
def profile_context(tmp_path, monkeypatch):
    monkeypatch.delenv('ORBIT_OPERATIONS_DATA_ROOT', raising=False)
    monkeypatch.delenv('ORBIT_OPERATIONS_PROFILE', raising=False)
    monkeypatch.setenv('LOCALAPPDATA', str(tmp_path / 'appdata'))
    source = tmp_path / 'source'; source.mkdir()
    caller = tmp_path / 'other-worktree'; caller.mkdir()
    data = tmp_path / 'sentinel'; data.mkdir()
    pointer = tmp_path / 'appdata/OrbitHive/operations-runtime.json'
    pointer.parent.mkdir(parents=True)
    payload = {'schema_version': 'orbit-operations-profile/v1', 'environment': 'stable',
               'source_root': str(source), 'data_root': str(data),
               'release_identity': {'code_version': 'fixture', 'environment': 'stable', 'manifest_digest': 'fixture'}}
    calls = []
    monkeypatch.setattr(guard, 'WorkbenchEngine', lambda path, release: calls.append((path, release)) or 'engine-spy')
    def save(): pointer.write_text(json.dumps(payload), encoding='utf-8')
    save()
    return SimpleNamespace(source=source, caller=caller, data=data, pointer=pointer, payload=payload, calls=calls, save=save)


@pytest.mark.parametrize('explicit', [False, True])
def test_unbound_worktree_cannot_open_shared_ledger(profile_context, monkeypatch, explicit):
    c = profile_context
    if explicit: monkeypatch.setenv('ORBIT_OPERATIONS_PROFILE', str(c.pointer))
    with pytest.raises(ValueError, match='caller'):
        guard.engine_for(c.caller)
    assert c.calls == []
    assert not (c.data / 'tasks.db').exists()


def test_registered_source_still_shares_ledger(profile_context):
    c = profile_context
    assert guard.engine_for(c.source) == 'engine-spy'
    assert c.calls == [(c.data / 'tasks.db', c.payload['release_identity'])]


def test_explicit_registered_skill_root_can_share(profile_context):
    c = profile_context
    c.payload['authorized_source_roots'] = [str(c.caller)]
    c.save()
    assert guard.engine_for(c.caller) == 'engine-spy'
    assert c.calls[0][0] == c.data / 'tasks.db'


@pytest.mark.parametrize('change', [{'source_root': None}, {'authorized_source_roots': ['relative']}, {'authorized_source_roots': '*'}])
def test_invalid_caller_bindings_fail_before_database(profile_context, change):
    c = profile_context; c.payload.update(change); c.save()
    with pytest.raises(ValueError, match='caller'):
        guard.engine_for(c.source)
    assert not c.calls


def test_explicit_runtime_data_root_keeps_configured_binding(profile_context, monkeypatch):
    c = profile_context
    monkeypatch.setenv('ORBIT_OPERATIONS_DATA_ROOT', str(c.data))
    monkeypatch.setattr(guard.RuntimeProfile, 'capture', lambda root: SimpleNamespace(environment='stable', data_root=c.data, version='fixture', manifest_digest='fixture'))
    assert guard.engine_for(c.source) == 'engine-spy'
    assert c.calls[0][0] == c.data / 'tasks.db'


def test_sibling_prefix_is_not_authorized(profile_context):
    c = profile_context
    sibling = c.source.with_name(c.source.name + '-untrusted'); sibling.mkdir()
    with pytest.raises(ValueError, match='caller'):
        guard.engine_for(sibling)
    assert not c.calls


def test_missing_registered_directory_does_not_grant_access(profile_context):
    c = profile_context
    c.payload['authorized_source_roots'] = [str(c.caller / 'missing')]; c.save()
    with pytest.raises(ValueError, match='caller'):
        guard.engine_for(c.source)
    assert not c.calls


def test_resolved_alias_of_authorized_directory_is_accepted(profile_context):
    c = profile_context
    alias = c.source.parent / 'authorized-alias'
    alias.symlink_to(c.source, target_is_directory=True)
    assert guard.engine_for(alias) == 'engine-spy'
    assert c.calls[0][0] == c.data / 'tasks.db'


@pytest.mark.skipif(os.name != 'nt', reason='Windows filesystem identity')
def test_windows_case_normalization(profile_context):
    c = profile_context
    assert guard.engine_for(str(c.source).upper()) == 'engine-spy'


def test_unrelated_shared_profile_fails_before_publication_or_common(profile_context):
    c = profile_context
    # Both entrypoints must reject before interpreting provider fixture payloads.
    for call in [lambda: guard.begin_publication({}, [], c.caller),
                 lambda: guard.begin_common({}, c.caller)]:
        with pytest.raises(ValueError, match='caller'):
            call()
    assert not c.calls
