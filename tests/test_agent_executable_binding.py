"""Synthetic path binding only: no CLI, auth, provider or service processes."""
import hashlib
import os
from pathlib import Path

import pytest

from shared_platform import operations_launch as launch


def _file(path, body=b'synthetic executor; never launched'):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(body)
    return path


def _discovery(root, executor):
    return {'agent_executable_discovery': {
        'bin_root': str(root), 'sha256': hashlib.sha256(executor.read_bytes()).hexdigest()}}


def _bind(config):
    return launch.bind_agent_executable(config)


def test_missing_explicit_path_blocks_and_clears_ambient_executor(tmp_path, monkeypatch):
    monkeypatch.setenv('ORBIT_OPERATIONS_AGENT_EXECUTABLE', 'ambient-codex.exe')
    result = _bind({'agent_executable': str(tmp_path / 'removed-version' / 'codex.exe')})
    assert result['status'] == 'BLOCKED'
    assert result['reason'] == 'NATIVE_AGENT_EXECUTABLE_MISSING'
    assert 'ORBIT_OPERATIONS_AGENT_EXECUTABLE' not in os.environ


def test_explicit_path_has_priority_over_discovery(tmp_path, monkeypatch):
    explicit = _file(tmp_path / 'explicit.exe', b'explicit')
    root = tmp_path / 'bin'
    candidate = _file(root / '2222222222222222' / 'codex.exe', b'discovered')
    result = _bind({'agent_executable': str(explicit), **_discovery(root, candidate)})
    assert result['path'] == str(explicit.resolve())
    assert result['source'] == 'explicit'
    assert result['status'] == 'PRESENT_UNVERIFIED'
    assert result['cli_verified'] is result['task_execution_verified'] is False
    assert os.environ['ORBIT_OPERATIONS_AGENT_EXECUTABLE'] == str(explicit.resolve())


def test_missing_explicit_path_never_silently_falls_back(tmp_path):
    root = tmp_path / 'bin'
    candidate = _file(root / '2222222222222222' / 'codex.exe')
    result = _bind({'agent_executable': str(root / '1111111111111111' / 'codex.exe'),
                    **_discovery(root, candidate)})
    assert result['status'] == 'BLOCKED' and result['path'] is None
    assert result['reason'] == 'NATIVE_AGENT_EXECUTABLE_MISSING'


def test_discovery_survives_changed_version_directory_with_same_reviewed_bytes(tmp_path):
    root = tmp_path / 'bin'
    candidate = _file(root / '2222222222222222' / 'codex.exe')
    config = _discovery(root, candidate)
    first = _bind(config)
    next_version = root / '3333333333333333'
    candidate.parent.rename(next_version)
    updated = _bind(config)
    assert first['path'] != updated['path']
    assert updated['path'] == str((next_version / 'codex.exe').resolve())
    assert updated['status'] == 'PRESENT_UNVERIFIED'
    assert updated['source'] == 'controlled_discovery'
    assert updated['sha256'] == first['sha256']


def test_discovery_rejects_new_unreviewed_binary_bytes(tmp_path):
    root = tmp_path / 'bin'
    candidate = _file(root / '2222222222222222' / 'codex.exe')
    config = _discovery(root, candidate)
    candidate.write_bytes(b'new release requiring a fresh reviewed digest')
    result = _bind(config)
    assert result['status'] == 'BLOCKED'
    assert result['reason'] == 'NATIVE_AGENT_EXECUTABLE_DIGEST_MISMATCH'


def test_discovery_requires_reviewed_digest_and_never_uses_path_environment(tmp_path, monkeypatch):
    root = tmp_path / 'bin'
    candidate = _file(root / '2222222222222222' / 'codex.exe')
    monkeypatch.setenv('PATH', str(candidate.parent))
    result = _bind({'agent_executable_discovery': {'bin_root': str(root)}})
    assert result['status'] == 'BLOCKED'
    assert result['reason'] == 'NATIVE_AGENT_EXECUTABLE_DIGEST_REQUIRED'


def test_discovery_rejects_ambiguous_version_directories_even_with_one_pinned_digest(tmp_path):
    root = tmp_path / 'bin'
    first = _file(root / '1111111111111111' / 'codex.exe', b'first')
    _file(root / '2222222222222222' / 'codex.exe', b'second')
    result = _bind(_discovery(root, first))
    assert result['status'] == 'BLOCKED'
    assert result['reason'] == 'NATIVE_AGENT_EXECUTABLE_DISCOVERY_AMBIGUOUS'


def test_discovery_excludes_same_name_outside_exact_direct_version_layout(tmp_path):
    root = tmp_path / 'bin'
    impostor = _file(root / 'not-a-version' / 'codex.exe')
    _file(root / 'codex.exe')
    _file(root / '1111111111111111' / 'nested' / 'codex.exe')
    result = _bind(_discovery(root, impostor))
    assert result['status'] == 'BLOCKED'
    assert result['reason'] == 'NATIVE_AGENT_EXECUTABLE_DISCOVERY_EMPTY'


@pytest.mark.parametrize('key', ['agent_executable', 'agent_executable_discovery'])
def test_relative_binding_never_depends_on_cwd(tmp_path, monkeypatch, key):
    root = tmp_path / 'bin'
    candidate = _file(root / '1111111111111111' / 'codex.exe')
    monkeypatch.chdir(tmp_path)
    config = ({'agent_executable': str(candidate.relative_to(tmp_path))}
              if key == 'agent_executable' else
              {'agent_executable_discovery': {'bin_root': 'bin',
               'sha256': hashlib.sha256(candidate.read_bytes()).hexdigest()}})
    result = _bind(config)
    assert result['status'] == 'BLOCKED'
    assert result['reason'] == 'NATIVE_AGENT_EXECUTABLE_ABSOLUTE_PATH_REQUIRED'


def test_explicit_digest_mismatch_blocks_without_discovery_fallback(tmp_path):
    executor = _file(tmp_path / 'explicit.exe')
    result = _bind({'agent_executable': str(executor), 'agent_executable_sha256': '0' * 64})
    assert result['reason'] == 'NATIVE_AGENT_EXECUTABLE_DIGEST_MISMATCH'
    assert 'ORBIT_OPERATIONS_AGENT_EXECUTABLE' not in os.environ


def test_no_config_preserves_required_capability(tmp_path):
    result = _bind({})
    assert result['status'] == 'BLOCKED'
    assert result['reason'] == 'NATIVE_FIXED_AGENT_EXECUTABLE_REQUIRED'


def test_discovery_inventory_is_bounded(tmp_path):
    root = tmp_path / 'bin'
    candidate = _file(root / '1111111111111111' / 'codex.exe')
    for number in range(33):
        (root / str(number)).mkdir()
    result = _bind(_discovery(root, candidate))
    assert result['reason'] == 'NATIVE_AGENT_EXECUTABLE_DISCOVERY_LIMIT_EXCEEDED'


def test_hard_link_executor_is_not_bound(tmp_path):
    executor = _file(tmp_path / 'executor.exe')
    linked = tmp_path / 'same-bytes.exe'
    os.link(executor, linked)
    result = _bind({'agent_executable': str(executor)})
    assert result['reason'] == 'NATIVE_AGENT_EXECUTABLE_UNSAFE_PATH'


def test_discovery_rejects_unknown_configuration_fields(tmp_path):
    root = tmp_path / 'bin'
    executor = _file(root / '1111111111111111' / 'codex.exe')
    config = _discovery(root, executor)
    config['agent_executable_discovery']['choose_newest'] = True
    result = _bind(config)
    assert result['reason'] == 'NATIVE_AGENT_EXECUTABLE_DISCOVERY_CONFIG_INVALID'
